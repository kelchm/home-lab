#!/usr/bin/env python3
"""Read-only progress display for the bulk coordinator, not SparkNest jobs."""

import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import sys
import time


def size(value):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return "{:.1f} {}".format(value, unit)
        value /= 1000


def display(root):
    inventory = json.loads((root / "inventory.json").read_text())
    journal = json.loads((root / "journal.json").read_text())
    items = {item["key"]: item for item in inventory["items"]}
    states = {key: value for key, value in journal["items"].items() if key in items}
    counts = collections.Counter(value["state"] for value in states.values())
    total = len(items)
    completed = counts["verified"]
    verified_bytes = sum(value.get("verification", {}).get("bytes", 0)
                         for value in states.values() if value["state"] == "verified")
    lines = ["SparkNest bulk ingestion  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "Content verified: {}/{} collections | {} | Failed: {} | Queued: {}".format(
                 completed, total, size(verified_bytes), counts["failed"], total - len(states))]
    active = [value for value in states.values() if value["state"] not in ("verified", "failed")]
    try:
        worker = json.loads((root / "worker.json").read_text())
        os.kill(worker["pid"], 0)
        lines.append("Worker process: running (PID {})".format(worker["pid"]))
    except (OSError, KeyError, ValueError):
        lines.append("Worker process: not found" if not journal.get("finished") else "Queue pass finished")
    if journal.get("blocked"):
        lines.append("BLOCKED: " + journal["blocked"])
    elif journal.get("finished"):
        lines.append("Queue pass finished; inspect failures and sharing warnings before acceptance.")
    for value in active:
        name = value["name"]
        if name.startswith("models--"):
            name = name[8:].replace("--", "/", 1)
        lines.extend(["", "Current: " + name, "Source: " + value["source"] + " / " + value["store"]])
        phase = value["state"]
        labels = {"staging": "Copying/cloning and checking source files",
                  "importing": "Checking existing blobs, native import and sharing",
                  "verifying": "Hashing the complete archived collection through FUSE"}
        lines.append("Phase: " + labels.get(phase, phase))
        if phase == "staging":
            lines.append("Staged: {}/{} files | {} of {}".format(
                value.get("done_files", 0), value["total_files"],
                size(value.get("staged_bytes", 0)), size(value["total_bytes"])))
            if value.get("current"):
                lines.append("Current file: {} ({})".format(value["current"], size(value.get("current_bytes", 0))))
        else:
            lines.append("Collection size: " + size(value["total_bytes"]))
    warnings = {}
    for key, value in states.items():
        notes = value.get("share_job", {}).get("job", {}).get("notes", [])
        skipped = [note for note in notes if ": kept " in note]
        if skipped:
            warnings.setdefault(items[key]["name"], set()).update(skipped)
    lines.extend(["", "Native sharing warnings: {} repos with skipped files".format(len(warnings)),
                  "Content verification does not mean sharing/deduplication succeeded."])
    for value in states.values():
        if value["state"] == "failed":
            lines.append("FAILED: {}: {}".format(value["name"], value.get("error", "").splitlines()[0][:180]))
    age = max(0, int(time.time() - (root / "journal.json").stat().st_mtime))
    lines.extend(["", "Journal last updated {}s ago; updates occur at file/stage boundaries.".format(age),
                  "Large-file hashing can take several minutes without an update."])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be at least one second")
    try:
        while True:
            output = display(args.runtime)
            if not args.once and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(output, flush=True)
            if args.once:
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped; the ingestion worker continues.")


if __name__ == "__main__":
    main()
