#!/usr/bin/env python3
"""Check every alert rule under kubernetes/ against the alert conventions in docs/runbooks/alerting.md."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SEVERITIES = {"critical", "warning", "info"}
# Names replaced when the conventions were adopted; a rule must not bring one back.
RETIRED = {
    "DriveAtaPendingSectors", "DriveAtaReallocatedSectors", "DriveAtaUncorrectableSectors", "DriveHealthMissing",
    "DriveNvmeControllerNotLive", "GLKVMHot", "GLKVMMetricsUnavailable", "HostCollectorDown", "HostCpuHot",
    "HostDeployFailing", "HostFilesystemLow", "HostLinkDown", "HostLinkFlapping", "HostMemoryStalled",
    "HostMetricsMissing", "HostNvmeHot", "HostOomKill", "HostQueueDropping", "KubernetesEventsIngestionSilent",
    "NasDiskHealthWarning", "NasDiskHot", "NasHardwareFault", "NasRaidDegraded", "NasUpsBatteryLow",
    "NasUpsOnBattery", "NodeCpuTemperatureCritical", "NodeCpuTemperatureHigh", "NodeCpuTemperatureSensorMissing",
    "NodeGpuTemperatureCritical", "NodeGpuTemperatureHigh", "NodeNvmeCompositeTemperatureCritical",
    "NodeNvmeCompositeTemperatureHigh", "NodeNvmeSensorTemperatureCritical", "NodeNvmeSensorTemperatureHigh",
    "NodeNvmeTemperatureSensorMissing", "NodePhysicalLinkSpeedDegraded", "PVEAPIUnavailable", "PVEGuestNotRunning",
    "PVENodeOffline", "PVEStorageNearlyFull", "PiProbeCertificateExpiring", "PiProbeFailed", "PiProbeSlow",
    "PiUpsBatteryLow", "PiUpsDataStale", "PiUpsOnBattery", "PiUpsRuntimeLow", "SparkGpuThermalThrottling",
    "StarlinkMetricsUnavailable", "TMobileMetricsUnavailable", "UniFiDeviceDisconnected", "UniFiDeviceHot",
    "UniFiMetricsStale", "UniFiSyslogSilent", "UniFiWANOffline",
}


def documents(path):
    """Each YAML document in a file, as JSON from yq."""
    out = subprocess.run(["yq", "-o=json", "-I=0", ".", str(path)], check=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.strip() not in ("", "null"):
            yield json.loads(line)


def rules(node):
    """Every alerting rule in a document, wherever it sits: a VMRule, a PrometheusRule or chart values."""
    if isinstance(node, dict):
        if "alert" in node and "expr" in node:
            yield node
        for value in node.values():
            yield from rules(value)
    elif isinstance(node, list):
        for value in node:
            yield from rules(value)


errors, count = [], 0
for path in sorted((ROOT / "kubernetes").rglob("*.y*ml")):
    if path.suffix not in (".yaml", ".yml") or path.stem.endswith(".sops"):
        continue
    for document in documents(path):
        for rule in rules(document):
            count += 1
            name, where = rule["alert"], path.relative_to(ROOT)
            annotations = rule.get("annotations") or {}
            if not NAME.match(name):
                errors.append(f"{where}: {name}: a name is CamelCase and starts with a capital")
            if name in RETIRED:
                errors.append(f"{where}: {name}: this name was retired; see the alert conventions")
            severity = (rule.get("labels") or {}).get("severity")
            if severity not in SEVERITIES:
                errors.append(f"{where}: {name}: severity is {severity!r}, not one of {sorted(SEVERITIES)}")
            for key in ("summary", "description"):
                if not annotations.get(key):
                    errors.append(f"{where}: {name}: no {key} annotation")

print("\n".join(errors) if errors else f"{count} alert rules follow the alert conventions.")
sys.exit(1 if errors else 0)
