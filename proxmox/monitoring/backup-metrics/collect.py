#!/usr/bin/env python3
"""Publish read-only PVE backup observations to node-exporter's textfile collector."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def query(path, **parameters):
    command = ["pvesh", "get", path, "--output-format", "json"]
    for key, value in parameters.items():
        command.extend(["--" + key.replace("_", "-"), str(value)])
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
    value = json.loads(result.stdout)
    if not isinstance(value, list):
        raise ValueError(f"Expected a list from {path}")
    return value


def labels(**values):
    return "{" + ",".join(f"{key}={json.dumps(str(value))}" for key, value in values.items()) + "}"


def observe(read, cluster, storage, now):
    status = read("/cluster/status")
    clusters = [x for x in status if x.get("type") == "cluster"]
    local = [x for x in status if x.get("type") == "node" and x.get("local") == 1]
    if len(clusters) != 1 or clusters[0].get("name") != cluster or len(local) != 1:
        raise ValueError("Unexpected cluster or local node identity")
    node = local[0]["name"]
    guests = read("/cluster/resources", type="vm")
    local_guests = {str(x["vmid"]): x for x in guests if x.get("node") == node}
    persistent = {vmid: x for vmid, x in local_guests.items()
                  if x.get("template", 0) == 0 and "persistent" in x.get("tags", "").split(";")}
    # Never treat a partial task list as the complete observation window.
    tasks = read(f"/nodes/{node}/tasks", typefilter="vzdump", source="archive", since=int(now - 7 * 86400), limit=1000)
    if len(tasks) >= 1000:
        raise ValueError("Backup task history exceeded the 1000-task collection bound")
    latest = {}
    for task in tasks:
        if task.get("type") != "vzdump" or "endtime" not in task:
            raise ValueError("Unexpected completed backup task")
        vmid = str(task.get("id", ""))
        if vmid and vmid not in local_guests:
            continue  # Removed or migrated guests no longer belong to this node.
        scope = vmid or "batch"
        if scope not in latest or (task["endtime"], task["starttime"]) > (latest[scope]["endtime"], latest[scope]["starttime"]):
            latest[scope] = task
    archives = read(f"/nodes/{node}/storage/{storage}/content", content="backup") if persistent else []
    newest = {vmid: 0 for vmid in persistent}
    for archive in archives:
        vmid = str(archive.get("vmid", ""))
        if vmid in newest:
            timestamp = archive.get("ctime")
            if not isinstance(timestamp, (int, float)) or timestamp <= 0 or timestamp > now + 120:
                raise ValueError("Invalid backup archive creation timestamp")
            newest[vmid] = max(newest[vmid], timestamp)
    lines = []
    for scope, task in sorted(latest.items()):
        end = task["endtime"]
        if not isinstance(end, (int, float)) or end <= 0 or end > now + 120 or not task.get("status"):
            raise ValueError("Invalid completed backup task status or time")
        identity = labels(cluster=cluster, scope=scope)
        lines.append(f"pve_backup_task_success{identity} {int(task['status'] == 'OK')}")
        lines.append(f"pve_backup_task_completed_timestamp_seconds{identity} {end}")
    for vmid, timestamp in sorted(newest.items()):
        identity = labels(cluster=cluster, id=f"{persistent[vmid]['type']}/{vmid}", storage=storage)
        lines.append(f"pve_backup_archive_timestamp_seconds{identity} {timestamp}")
    return lines


def publish(path, lines):
    # Same-directory rename is atomic; node-exporter never reads half a file.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".pve-backups-", delete=False) as file:
        temporary = Path(file.name)
        try:
            os.fchmod(file.fileno(), 0o644)
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(read, path, cluster, storage, now):
    identity = labels(cluster=cluster)
    try:
        lines = observe(read, cluster, storage, now)
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError, OSError) as error:
        # Preserve prior facts so a collection error cannot falsely resolve an
        # existing failure/staleness alert. Never invent zero archive times.
        prior = []
        try:
            prior = [line for line in path.read_text().splitlines()
                     if line.startswith(("pve_backup_task_", "pve_backup_archive_"))
                     and f"cluster={json.dumps(cluster)}" in line]
        except FileNotFoundError:
            pass
        publish(path, [f"pve_backup_collector_success{identity} 0",
                       f"pve_backup_collector_attempt_timestamp_seconds{identity} {now}", *prior])
        print(f"Backup metrics collection failed: {type(error).__name__}", file=sys.stderr)
        return 1
    publish(path, [f"pve_backup_collector_success{identity} 1",
                   f"pve_backup_collector_attempt_timestamp_seconds{identity} {now}", *lines])
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", default="pve-sbx")
    parser.add_argument("--storage", default="backups-pve-sbx")
    parser.add_argument("--output", type=Path, default=Path("/var/lib/node_exporter/textfile_collector/pve-backups.prom"))
    args = parser.parse_args()
    return run(query, args.output, args.cluster, args.storage, int(time.time()))


if __name__ == "__main__":
    sys.exit(main())
