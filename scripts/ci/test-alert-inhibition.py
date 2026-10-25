#!/usr/bin/env python3
"""Run the one-alert-per-machine inhibit rules in a real Alertmanager and check which simulated alerts each outage holds back."""
import datetime as dt
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "kubernetes/apps/observability/victoria-metrics-k8s-stack/app/one-alert-per-machine.yaml"

K = {"cluster": "k8s-prod"}
P = {"cluster": "pve-sbx"}
# Each scenario: the machine's down alert, then (alert, labels, held back?) for alerts firing alongside it.
SCENARIOS = {
    "k8s-prod-2 is down": [
        ("KubeNodeDown", {**K, "node": "k8s-prod-2", "instance": "k8s-prod-2", "severity": "critical"}, False),
        ("LonghornNodeNotReady", {**K, "node": "k8s-prod-2", "namespace": "longhorn-system", "severity": "critical"}, True),
        ("KubeNodeNotReady", {**K, "node": "k8s-prod-2", "namespace": "observability", "severity": "warning"}, True),
        ("KubeNodeUnreachable", {**K, "node": "k8s-prod-2", "namespace": "observability", "severity": "warning"}, True),
        ("KubeletInstanceUnreachable", {**K, "node": "k8s-prod-2", "instance": "k8s-prod-2", "severity": "warning"}, True),
        ("FluentBitCollectorTargetMissing", {**K, "node": "k8s-prod-2", "severity": "critical"}, True),
        ("DriveMetricsMissing", {"instance": "k8s-prod-2", "device": "nvme0", "platform": "k8s", "severity": "warning"}, True),
        ("NodeCPUTemperatureSensorMissing", {**K, "instance": "k8s-prod-2", "severity": "warning"}, True),
        ("KubeAPIInstanceUnreachable", {**K, "instance": "10.32.30.12:6443", "job": "apiserver", "severity": "warning"}, True),
        ("KubeSchedulerInstanceUnreachable", {**K, "instance": "10.32.30.12:10259", "job": "kube-scheduler", "severity": "warning"}, True),
        # A fault the machine still reports, what the outage breaks, and other machines.
        ("SmartCTLDeviceMediaErrors", {"instance": "k8s-prod-2", "device": "nvme0", "severity": "critical"}, False),
        ("NodeCPUTemperatureCritical", {**K, "instance": "k8s-prod-2", "severity": "critical"}, False),
        ("LonghornVolumeFaulted", {**K, "node": "k8s-prod-2", "namespace": "longhorn-system", "volume": "pvc-1", "severity": "critical"}, False),
        ("KubePodNotReady", {**K, "namespace": "media", "pod": "jellyfin-0", "severity": "warning"}, False),
        ("LonghornNodeNotReady", {**K, "node": "k8s-prod-1", "namespace": "longhorn-system", "severity": "critical"}, False),
        ("KubeSchedulerInstanceUnreachable", {**K, "instance": "10.32.30.13:10259", "job": "kube-scheduler", "severity": "warning"}, False),
        ("DriveMetricsMissing", {"instance": "k8s-prod-1", "device": "nvme0", "platform": "k8s", "severity": "warning"}, False),
        ("TargetDown", {**K, "job": "node-exporter", "namespace": "observability", "severity": "warning"}, False),
        ("etcdMembersDown", {**K, "job": "kube-etcd", "severity": "warning"}, False),
    ],
    "pve-sbx-1 is down": [
        ("PVENodeDown", {**P, "id": "node/pve-sbx-1", "node": "pve-sbx-1", "instance": "pve-sbx-1", "severity": "critical"}, False),
        ("NodeMetricsMissing", {"instance": "pve-sbx-1", "platform": "pve", "severity": "warning"}, True),
        ("PVEGuestDown", {**P, "id": "qemu/103", "node": "pve-sbx-1", "severity": "warning"}, True),
        ("DriveMetricsMissing", {"instance": "pve-sbx-1", "device": "nvme0", "platform": "pve", "severity": "warning"}, True),
        ("NodeCollectorDown", {"instance": "pve-sbx-1", "job": "pve-smartctl", "platform": "pve", "severity": "warning"}, True),
        ("NodeCPUTemperatureCritical", {"instance": "pve-sbx-1", "platform": "pve", "severity": "critical"}, False),
        ("PVEGuestDown", {**P, "id": "qemu/150", "severity": "warning"}, False),
        ("PVEGuestDown", {**P, "id": "qemu/101", "node": "pve-sbx-2", "severity": "warning"}, False),
        ("NodeMetricsMissing", {"instance": "pve-sbx-2", "platform": "pve", "severity": "warning"}, False),
    ],
    "the NAS is dark": [
        ("NodeMetricsMissing", {"instance": "nas", "platform": "synology", "severity": "warning"}, False),
        ("DriveMetricsMissing", {"instance": "nas", "device": "sata1", "platform": "synology", "severity": "warning"}, True),
        ("NodeCollectorDown", {"instance": "nas", "job": "synology-snmp", "platform": "synology", "severity": "warning"}, True),
        ("NASRAIDDegraded", {"instance": "nas", "raidName": "Storage Pool 1", "severity": "critical"}, False),
        ("TargetDown", {**K, "job": "cnpg", "namespace": "cnpg-system", "severity": "warning"}, False),
        ("UPSOnBattery", {"instance": "ups-compute-rack", "ups": "compute-rack", "severity": "warning"}, False),
    ],
}


def inhibit_rules():
    out = subprocess.run(["yq", "-o=json", "-I=0", ".spec.inhibit_rules", str(RULES)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def post(base, alerts, ends):
    api(f"{base}/api/v2/alerts", [{"labels": {"alertname": n, **l}, "endsAt": ends} for n, l, _ in alerts])


def main():
    if not shutil.which("alertmanager"):
        sys.exit("alertmanager is not on PATH; it is pinned in .mise.toml")
    work = Path(tempfile.mkdtemp())
    config = {"route": {"receiver": "none"}, "receivers": [{"name": "none"}], "inhibit_rules": inhibit_rules()}
    (work / "alertmanager.yml").write_text(json.dumps(config))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    am = subprocess.Popen(["alertmanager", f"--config.file={work / 'alertmanager.yml'}", f"--storage.path={work / 'data'}",
                           f"--web.listen-address=127.0.0.1:{port}", "--cluster.listen-address="],
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    failures = []
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/-/ready")
                break
            except OSError:
                if am.poll() is not None:
                    sys.exit(f"alertmanager exited: {am.stderr.read()}")
                time.sleep(0.1)
        now = dt.datetime.now(dt.timezone.utc)
        for title, alerts in SCENARIOS.items():
            post(base, alerts, (now + dt.timedelta(hours=1)).isoformat())
            time.sleep(0.5)
            live = api(f"{base}/api/v2/alerts?active=true&inhibited=true&silenced=true")
            held = {(a["labels"]["alertname"], json.dumps(a["labels"], sort_keys=True)): bool(a["status"]["inhibitedBy"]) for a in live}
            for name, labels, want in alerts:
                got = held.get((name, json.dumps({"alertname": name, **labels}, sort_keys=True)))
                if got is not want:
                    failures.append(f"{title}: {name} {labels} should {'be held back' if want else 'notify'}")
            post(base, alerts, (now - dt.timedelta(seconds=1)).isoformat())
            time.sleep(0.5)
    finally:
        am.terminate()
        am.wait()
        shutil.rmtree(work, ignore_errors=True)
    if failures:
        sys.exit("\n".join(failures))
    print(f"{len(SCENARIOS)} outages hold back exactly the alerts they should.")


main()
