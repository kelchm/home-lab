"""Widgets by class of thing: each is built once and stamped out per system, platform or device.

Series identify themselves by `platform` and `instance` (docs/runbooks/host-monitoring.md#identity), and S() turns
that into the system they are shown under. The UPS mapping (U()) merges the two collectors' metric names in the query.
"""
import json
import re
import urllib.parse

from .schema import (BROKEN, FINE, GAUGE, HEADROOM, LOAD, LOOK, NEUTRAL, NODASH, NOTICE, PROBLEM, QUIET, ROOM_TILE, RUNS_OUT, TEXT, TILE_LOAD,
                     ALL, RED, Panels, add_links, fixed, lq, ls, match, q, qvar, th, union, words, yes, zero)

K = 'cluster="k8s-prod"'
HOSTS = 'job=~"node-exporter|(pve|spark|synology|pi)-node",platform!=""'
FS = 'fstype=~"ext4|xfs|btrfs"'

# ================================================================== systems
# A system is one row of the board. k8s-prod's series also carry their cluster; every other series is placed by its platform.
# A UPS belongs to power whichever machine reads it, and the probes run from a Pi belong to what they probe.
SYSTEMS = ["k8s-prod", "pve-sbx", "spark", "athena", "network", "power", "devices"]
PLATFORMS = {"k8s-prod": "k8s", "pve-sbx": "pve", "spark": "spark", "athena": "synology", "network": "unifi|starlink|tmobile", "devices": "pi|glkvm"}
ALERT_FAMILIES = {"network": "(UniFi|Starlink|TMobile).*", "athena": "NAS.*", "devices": "GLKVM.*", "power": "UPS.*", "k8s-prod": "Probe.*"}


def S(expr):
    """Label each series with the system it belongs to; anything left over shows up as "unassigned" rather than disappearing."""
    e = expr
    for c in ("k8s-prod", "pve-sbx"):
        e = f'label_replace({e}, "system", "{c}", "cluster", "{c}")'
    for s, p in PLATFORMS.items():
        e = f'label_replace({e}, "system", "{s}", "platform", "{p}")'
    for s, a in ALERT_FAMILIES.items():
        e = f'label_replace({e}, "system", "{s}", "alertname", "{a}")'
    e = f'label_replace(label_replace({e}, "system", "power", "job", "pi-nut"), "system", "k8s-prod", "job", "pi-blackbox")'
    e = f'label_replace({e}, "system", "network", "probe", "unifi-gateway")'
    return f'label_replace({e}, "system", "unassigned", "system", "")'


SYS = f'sys(x) = {S("x")}'
# A symbol per system, because a tab title can only hold text. U+FE0E asks for the plain form of a symbol that also exists as a coloured emoji.
GLYPH = {"Everything": "⌂", "k8s-prod": "⎈", "pve-sbx": "▤", "spark": "✦", "athena": "⛁", "network": "⇄", "power": "⚡︎", "devices": "⌨︎"}


def tab_slug(name):
    """How Grafana writes a tab titled "<symbol> <name>" in ?dtab=."""
    return f"{GLYPH[name]}-{name}"


def QS(body):
    return f"WITH ({SYS}) {body}"


def explore(expr):
    """Explore, showing the lines a LogsQL query matches over the dashboard's time range. Variables stay unencoded so Grafana fills them in."""
    pane = {"lab": {"datasource": "victorialogs", "queries": [{"refId": "A", "datasource": {"type": "victoriametrics-logs-datasource", "uid": "victorialogs"},
            "editorMode": "code", "queryType": "instant", "expr": expr}], "range": {"from": "${__from}", "to": "${__to}"}}}
    text = urllib.parse.quote(json.dumps(pane, separators=(",", ":")), safe="")
    for v in set(re.findall(r"\$\{[^}]+\}", json.dumps(pane))):
        text = text.replace(urllib.parse.quote(v, safe=""), v)
    return "/explore?schemaVersion=1&orgId=1&panes=" + text


def to(title, url):
    return {"title": title, "url": url}


W = Panels()
PCT = dict(unit="percent", min=0, max=100)
LOADBAR = dict(PCT, custom__cellOptions=GAUGE, thresholds=th(LOAD))
FILLBAR = dict(PCT, custom__cellOptions=GAUGE, thresholds=RUNS_OUT)


def CTEXT(*steps):
    return dict(custom__cellOptions=TEXT, thresholds=th([{"value": None, "color": "text"}] + [{"value": v, "color": c} for v, c in steps]))


# ================================================================== Linux machines, one platform at a time
def sensor(p, label, metric="node_hwmon_temp_celsius"):
    return f'max by (instance) ({metric}{{platform="{p}"}} * on (instance, chip, sensor) group_left node_hwmon_sensor_label{{platform="{p}",label="{label}"}})'


def chip(p, name):
    return f'max by (instance) (node_hwmon_temp_celsius{{platform="{p}"}} * on (instance, chip) group_left node_hwmon_chip_names{{platform="{p}",chip_name="{name}"}})'


def machine_cpu(p):
    return f'100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{{{HOSTS},platform="{p}",mode="idle"}}[5m])))'


def machines(p):
    hs = f'{HOSTS},platform="{p}"'
    temp = f'{sensor(p, "Tctl")} or {sensor(p, "Package id 0")} or max by (instance) (nvidia_smi_temperature_gpu{{platform="{p}"}}) or {chip(p, "k10temp")} or {chip(p, "cpu_thermal")}'
    # The AMD machines publish no limit, so 95 °C is assumed for them, and 85 °C, where a Pi 5 throttles, for the Pis; the Intel hosts and the Sparks publish their own.
    room = (f'(95 - {sensor(p, "Tctl")}) or ({sensor(p, "Package id 0", "node_hwmon_temp_crit_celsius")} - {sensor(p, "Package id 0")})'
            f' or max by (instance) (nvidia_smi_temperature_gpu_tlimit{{platform="{p}"}}) or (95 - {chip(p, "k10temp")}) or (85 - {chip(p, "cpu_thermal")})')
    P = Panels()
    P.table("machines", "Machines", "instance", "Machine", [
        ("CPU", machine_cpu(p), LOADBAR),
        ("Memory", f'100 * (1 - max by (instance) (node_memory_MemAvailable_bytes{{{hs}}} / node_memory_MemTotal_bytes{{{hs}}}))', LOADBAR),
        ("Fullest disk", f'max by (instance) (100 * (1 - node_filesystem_avail_bytes{{{hs},{FS}}} / (node_filesystem_size_bytes{{{hs},{FS}}} > 0)))', FILLBAR),
        ("Hottest part", temp, dict(unit="celsius")),
        ("To its limit", room, dict(unit="celsius", custom__cellOptions=TEXT, thresholds=HEADROOM, mappings=NODASH)),
        ("Up for", f'max by (instance) (time() - node_boot_time_seconds{{{hs}}})', dict(unit="dtdurations"))],
        desc="The same table for any group of Linux machines. Raw temperature carries no colour because a CPU and a GPU run at different temperatures; the colour is on how far each machine is from its own limit.")
    if p in MACHINE_LINKS:
        add_links(P.elements["machines"], "Machine", MACHINE_LINKS[p])
    return P.elements["machines"]


# Where a machine's own detail is.
MACHINE_LINKS = {"k8s": [to("${__value.text} in Node Exporter Full", "/d/rYdddlPWk?var-job=node-exporter&var-nodename=${__value.raw}&var-node=${__value.raw}&${__url_time_range}"),
                         to("Talos logs from ${__value.text}", explore('service.namespace:=talos k8s.node.name:="${__value.raw}"'))]}


def machine_cpu_trend(p):
    P = Panels()
    P.trend("machine-cpu", "CPU", [("{{instance}}", machine_cpu(p))], "percent", hi=100, desc="CPU in use on each machine of one platform.")
    return P.elements["machine-cpu"]


# ================================================================== drives: solid-state and hard disks are different classes
HEALTH = dict(mappings=words(_1=("Healthy", None), _2=("Warning", "orange"), _0=("Failing", "red")), custom__cellOptions=TEXT)


def ssd_drives(p):
    def ssd(metric):
        return f'max by (drive) (label_join({metric}{{platform="{p}",device=~"nvme.*"}}, "drive", " ", "instance", "device"))'
    P = Panels()
    P.table("ssd-drives", "Drives · solid state", "drive", "Drive", [
        ("Health", ssd("drive:health_status"), HEALTH),
        ("Worn", ssd("smartctl_device_percentage_used"), dict(PCT, custom__cellOptions=GAUGE, thresholds=RUNS_OUT)),
        ("Spare left", ssd("smartctl_device_available_spare"), dict(unit="percent", custom__cellOptions=TEXT, mappings=NODASH,
                                                                    thresholds=th([{"value": None, "color": "red"}, {"value": 10, "color": "orange"}, {"value": 30, "color": "text"}]))),
        ("Temperature", ssd("drive:temperature_celsius"), dict(unit="celsius", **CTEXT((68, "orange"), (78, "red")))),
        ("Media errors", ssd("smartctl_device_media_errors"), CTEXT((1, "red"))),
        ("Powered on", ssd("smartctl_device_power_on_seconds"), dict(unit="dtdurations"))],
        info=(f'max by (drive, model_name) (label_join(drive:info{{platform="{p}",device=~"nvme.*"}}, "drive", " ", "instance", "device"))', {"model_name": "Model"}), keywidth=150,
        desc="What each NVMe drive says about itself, the same on every platform. Temperature turns red at 78 °C, about where the alert fires, 5 °C under these drives' own 83 °C and 84 °C warning temperatures, and orange 10 °C before.")
    return P.elements["ssd-drives"]


def hdd(metric, label="device"):
    return f'max by (disk) (label_replace({metric}{{platform="synology"}}, "disk", "$1", "{label}", "(.*)"))'


W.table("hdd-drives", "Drives · hard disks", "disk", "Disk", [
    ("Health", hdd("drive:health_status"), HEALTH),
    ("In use", hdd("diskStatus", "diskID"), dict(mappings=words(_1=("In a pool", None), _2=("Initialised", LOOK), _3=("Not in use", LOOK), _4=("System partition failed", "red"), _5=("Crashed", "red")),
                                                custom__cellOptions=TEXT)),
    ("Temperature", hdd("drive:temperature_celsius"), dict(unit="celsius", **CTEXT((45, "orange"), (50, "red")))),
    ("Reallocated", hdd("drive:ata_reallocated_sectors"), CTEXT((1, "red"))),
    ("Pending", hdd("drive:ata_pending_sectors"), CTEXT((1, "red"))),
    ("Uncorrectable", hdd("drive:ata_uncorrectable_sectors"), CTEXT((1, "red")))],
    info=('max by (disk, model_name) (label_replace(drive:info{platform="synology"}, "disk", "$1", "device", "(.*)"))', {"model_name": "Model"}), keywidth=90,
    desc="What each disk in the NAS reports through DSM. Hard disks wear out by growing bad sectors rather than by a wear figure, so these are counted instead.")

# ================================================================== Kubernetes cluster
W.tiles("k8s-state", "Cluster state · k8s-prod", [
    ("Nodes ready", f'sum(kube_node_status_condition{{condition="Ready",status="true",{K}}})', {}),
    ("Pods running", f'count(kube_pod_status_phase{{phase="Running",{K}}} == 1)', {}),
    ("Pods not ready", zero(f'sum(kube_pod_status_ready{{condition="false",{K}}} * on (namespace, pod) group_left kube_pod_status_phase{{phase="Running",{K}}})'
                            f' + (count(kube_pod_status_phase{{phase=~"Pending|Failed|Unknown",{K}}} == 1) or vector(0))'), dict(thresholds=PROBLEM)),
    ("Short of replicas", zero(f'sum(kube_deployment_status_replicas_unavailable{{{K}}}) + sum(kube_statefulset_status_replicas{{{K}}} - kube_statefulset_status_replicas_ready{{{K}}})'
                               f' + sum(kube_daemonset_status_number_unavailable{{{K}}})'), dict(thresholds=PROBLEM)),
    ("Restarts in 24 hours", zero(f'round(sum(increase(kube_pod_container_status_restarts_total{{{K}}}[24h])))'), dict(thresholds=th(QUIET + [{"value": 5, "color": "orange"}]))),
    ("Deploys failing", zero(f'count(flux_resource_info{{ready!="True",suspended!="True",{K}}})'), dict(thresholds=BROKEN)),
    ("Volumes unhealthy", zero(f'sum(longhorn_volume_robustness{{state=~"degraded|faulted",{K}}})'), dict(thresholds=BROKEN))],
    desc="Two counts for scale, then five counts of things that are wrong. A tile lights up only when its count is above zero.")
W.bars("k8s-capacity", "Capacity · k8s-prod", union(
    ls(f'100 * sum(kube_pod_container_resource_requests{{resource="cpu",{K}}}) / sum(kube_node_status_allocatable{{resource="cpu",{K}}})', name="CPU promised"),
    ls(f'100 * sum(kube_pod_container_resource_requests{{resource="memory",{K}}}) / sum(kube_node_status_allocatable{{resource="memory",{K}}})', name="Memory promised"),
    ls(f'100 * sum(kube_pod_info{{{K}}}) / sum(kube_node_status_allocatable{{resource="pods",{K}}})', name="Pod slots taken"),
    ls(f'100 * sum(longhorn_node_storage_usage_bytes{{{K}}}) / sum(longhorn_node_storage_capacity_bytes{{{K}}})', name="Longhorn disk used"),
    ls(f'100 * max(etcd_mvcc_db_total_size_in_bytes{{{K}}} / etcd_server_quota_backend_bytes{{{K}}})', name="etcd database")), "{{name}}",
    desc="What the cluster has promised or used, against what it has. Orange at 80%, red at 90%.")
FX = f"flux_resource_info{{{K}}}"
W.table("k8s-deploys", "Deploys · k8s-prod", "kind", "Kind", [
    ("Ready", f'count by (kind) (flux_resource_info{{ready="True",{K}}})', {}),
    ("Failing", f'count by (kind) (flux_resource_info{{ready!="True",suspended!="True",{K}}}) or count by (kind) ({FX}) * 0', CTEXT((1, "red"))),
    ("Paused", f'count by (kind) (flux_resource_info{{suspended="True",{K}}}) or count by (kind) ({FX}) * 0', CTEXT((1, "orange")))],
    desc="What Flux has applied from Git: ready, failing, and paused by hand.")
W.table("k8s-namespaces", "Namespaces · k8s-prod", "namespace", "Namespace", [
    ("Pods", f'count by (namespace) (kube_pod_status_phase{{phase="Running",{K}}} == 1)', {}),
    ("CPU cores", f'sum by (namespace) (rate(container_cpu_usage_seconds_total{{container!="",{K}}}[5m]))', dict(decimals=2)),
    ("Memory", f'sum by (namespace) (container_memory_working_set_bytes{{container!="",{K}}})', dict(unit="bytes", decimals=1)),
    ("Restarts", f'round(sum by (namespace) (increase(kube_pod_container_status_restarts_total{{{K}}}[24h])))', CTEXT((5, "orange")))],
    sort="CPU cores", desc="Where the cluster's CPU and memory are going. Restarts are counted over the last 24 hours.")
add_links(W.elements["k8s-namespaces"], "Namespace", [to("Workloads in ${__value.text}", "/d/a87fb0d919ec0ea5f6543124e16c42a5?var-namespace=${__value.raw}&${__url_time_range}"),
                                                      to("Logs from ${__value.text}", explore('k8s.namespace.name:="${__value.raw}" (log.iostream:* OR (service.name:=kubernetes-events severity_number:>=13))')),
                                                      to("Events in ${__value.text}", explore('{service.name="kubernetes-events"} k8s.namespace.name:="${__value.raw}"'))])
W.tiles("volumes", "Volumes · k8s-prod", [
    ("Healthy", zero(f'sum(longhorn_volume_robustness{{state="healthy",{K}}})'), {}),
    ("Degraded", zero(f'sum(longhorn_volume_robustness{{state="degraded",{K}}})'), dict(thresholds=PROBLEM)),
    ("Faulted", zero(f'sum(longhorn_volume_robustness{{state="faulted",{K}}})'), dict(thresholds=BROKEN)),
    ("Backup target", zero(f'max(longhorn_backup_target_available{{{K}}})'), dict(mappings=yes("Reachable", "Unreachable"), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}])))],
    wide=True, desc="Longhorn volumes by state, and whether the place backups go can be reached.")

# ================================================================== Proxmox cluster
GI = 'max by (id, name) (pve_guest_info{template="0"})'


def G(metric):
    return f"max by (name) (max by (id) ({metric}) * on (id) group_left(name) {GI})"


GUEST = 'max by (id) (pve_up{id=~"(qemu|lxc)/.*"}) and on (id) pve_guest_info{template="0"}'
W.tiles("pve-state", "Cluster state · pve-sbx", [
    ("Nodes online", 'count(max by (id) (pve_up{id=~"node/.*"}) == 1)', {}),
    ("Quorum", 'max(label_value(pve_cluster_info, "quorate"))', dict(mappings=yes("Yes", "No"), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("Guests running", zero(f"count(({GUEST}) == 1)"), {}),
    ("Guests stopped", zero(f"count(({GUEST}) == 0)"), dict(thresholds=PROBLEM)),
    ("Guests without a backup", zero("max(pve_not_backed_up_total)"), dict(thresholds=PROBLEM))],
    wide=True, desc="Nodes, quorum and guests, in the same shape as the Kubernetes tiles.")
W.table("pve-guests", "Guests · pve-sbx", "name", "Guest", [
    ("State", G("pve_up"), dict(mappings=yes("Running", "Stopped", "orange"), custom__cellOptions=TEXT)),
    ("CPU", f'100 * {G("pve_cpu_usage_ratio")}', LOADBAR),
    ("Memory", f'clamp_max(100 * {G("pve_memory_usage_bytes")} / {G("pve_memory_size_bytes")}, 100)', LOADBAR),
    ("Up for", G("pve_uptime_seconds"), dict(unit="dtdurations"))],
    info=('max by (name, node, type) (pve_guest_info{template="0"})', {"node": "Node", "type": "Type"}), keywidth=150,
    desc="Every virtual machine and container, where it runs and what it uses of what it was given.")
POOL = ('max by (id) (pve_disk_usage_bytes{id=~"storage/.*"} / (pve_disk_size_bytes{id=~"storage/.*"} > 0))'
        ' * on (id) group_left(storage, node, plugintype) max by (id, storage, node, plugintype) (pve_storage_info)')
SHARED = ls(f'100 * max by (storage) (label_match({POOL}, "plugintype", "nfs"))', node="shared")
W.bars("pve-pools", "Storage pools · pve-sbx", f'sort_desc(100 * max by (node, storage) (label_mismatch({POOL}, "plugintype", "nfs")) or {SHARED})', "{{node}} · {{storage}}",
       desc="How full each storage pool is. A pool shared by every node is shown once.")

# ================================================================== GPU host and inference
def nv(metric):
    return f'max(nvidia_smi_{metric}{{instance="$gpu"}})'


W.tiles("gpu", "GPU · $gpu", [
    ("In use", f'100 * {nv("utilization_gpu_ratio")}', dict(unit="percent", thresholds=TILE_LOAD)),
    ("Temperature", nv("temperature_gpu"), dict(unit="celsius")),
    ("To its limit", nv("temperature_gpu_tlimit"), dict(unit="celsius", thresholds=ROOM_TILE)),
    ("Power", nv("power_draw_watts"), dict(unit="watt"))],
    desc="A GPU at full use is doing its job, so use is coloured as load and never as a problem. Heat is judged by how far the GPU is from the limit it reports itself.")
W.tiles("inference", "Inference · $model", [
    ("Requests running", zero("sum(tensorfold:requests_running)"), {}),
    ("Requests waiting", zero("sum(tensorfold:requests_waiting)"), dict(thresholds=th(QUIET + [{"value": 4, "color": "orange"}]))),
    ("Tokens per second", zero("sum(rate(tensorfold:generation_tokens_total[5m]))"), dict(decimals=1)),
    ("Tokens in 24 hours", zero("sum(increase(tensorfold:generation_tokens_total[24h]))"), dict(unit="short", decimals=0)),
    ("Context cache in use", zero("100 * max(tensorfold:kv_cache_usage_ratio)"), dict(unit="percent", thresholds=TILE_LOAD))],
    desc="What the model is doing: work in hand, work queued, and how fast it is writing. Empty while no model is loaded.")
W.add("inference-idle", "Inference", "stat", [q("count(tensorfold:kv_cache_usage_ratio)")],
      {"colorMode": "none", "graphMode": "none", "justifyMode": "center", "orientation": "auto", "reduceOptions": RED, "textMode": "value", "text": {"valueSize": 16}},
      {"noValue": "No model is being served", "color": fixed("text")},
      desc="Shown while no model reports to the inference scrape; the model's own widgets appear in its place once one is loaded.")
W.trend("gpu-use", "GPU in use", [("{{instance}}", "100 * max by (instance) (nvidia_smi_utilization_gpu_ratio)")], "percent", hi=100, desc="How hard each GPU has been working.")
W.trend("tokens", "Tokens per second · $model", [("written", "sum(rate(tensorfold:generation_tokens_total[5m]))"), ("read", "sum(rate(tensorfold:prompt_tokens_total[5m]))")], "short",
        desc="Tokens read from prompts and written in answers.")

# ================================================================== NAS
SYN = 'job="synology-snmp"'
OKFAIL = words(_1=("Normal", None), _2=("Failed", "red"))
FAILED = th(QUIET + [{"value": 2, "color": "red"}])
# DSM's raidStatus values. Work in progress is described rather than coloured, apart from a repair or resync, which runs with less redundancy.
RAID = words(_1=("Normal", None), _2=("Repairing", "orange"), _3=("Migrating", None), _4=("Expanding", None), _5=("Deleting", None), _6=("Creating", None), _7=("Syncing", "orange"),
             _8=("Parity check", None), _9=("Assembling", None), _10=("Cancelling", None), _11=("Degraded", "red"), _12=("Crashed", "red"), _13=("Scrubbing", None))
W.tiles("nas-state", "NAS · athena", [
    ("System", f"max(systemStatus{{{SYN}}})", dict(mappings=OKFAIL, thresholds=FAILED)),
    ("Power", f"max(powerStatus{{{SYN}}})", dict(mappings=OKFAIL, thresholds=FAILED)),
    ("Fans", f"max(max(systemFanStatus{{{SYN}}}) or max(cpuFanStatus{{{SYN}}}))", dict(mappings=OKFAIL, thresholds=FAILED)),
    ("Storage pool", f'max(raidStatus{{{SYN},raidName=~"Storage Pool.*"}})', dict(mappings=RAID, thresholds=th(QUIET))),
    ("Disks in a pool", zero(f"count(diskStatus{{{SYN}}} == 1)"), {}),
    ("Disks not in use", zero(f"count(diskStatus{{{SYN}}} == 3)"), dict(thresholds=NOTICE)),
    ("Inside", f"max(temperature{{{SYN}}})", dict(unit="celsius")),
    ("DSM update", f"max(upgradeAvailable{{{SYN}}})", dict(mappings=words(_1=("Available", None), _2=("None", None)), thresholds=th(QUIET + [{"value": 1, "color": LOOK}, {"value": 2, "color": NEUTRAL}])))],
    wide=True, desc="What DSM reports about the box itself: its own status, power, fans, the storage pool and the disks.")
W.bars("nas-volumes", "Volumes · athena", f'100 * (1 - max by (raidName) (raidFreeSize{{{SYN},raidName=~"Volume.*"}}) / max by (raidName) (raidTotalSize{{{SYN},raidName=~"Volume.*"}}))', "{{raidName}}",
       desc="How full each NAS volume is, from DSM's own figures. The storage pool under it is always fully allocated, so it is not shown.")

# ================================================================== services
def svc(expr, by="svc"):
    e = f'label_replace({expr}, "svc", "$1", "exported_service", "(.*)")'
    e = f'label_replace({e}, "svc", "$1", "exported_service", "(.+)@.*")'
    return f'sum by ({by}) (label_replace({e}, "svc", "$1", "exported_service", "httproute-(.+)-gw-network-gateway.*"))'


REQ = svc(f"rate(traefik_service_requests_total{{{K}}}[1h])")
ERRORS = svc(f'rate(traefik_service_requests_total{{code=~"5..",{K}}}[1h])')
W.table("routes", "Routes · the last hour", "svc", "Service", [
    ("Requests per minute", f"60 * {REQ}", dict(decimals=1)),
    ("Errors", f"(100 * {ERRORS} / ({REQ} > 0)) or ({REQ} * 0)", dict(unit="percent", decimals=1, **CTEXT((1, "orange"), (5, "red")))),
    ("Slowest 5%", f'histogram_quantile(0.95, {svc(f"rate(traefik_service_request_duration_seconds_bucket{{{K}}}[1h])", "svc, le")})', dict(unit="s", decimals=1))],
    sort="Requests per minute", keywidth=230,
    desc="Traffic, failures and speed for every service Traefik fronts: the three numbers that describe any request-driven service.")
EXP = f"certmanager_certificate_expiration_timestamp_seconds{{{K}}}"
W.table("certs", "Certificates", "name", "Certificate", [
    ("Lifetime left", f"100 * min by (name) (({EXP} - time()) / (certmanager_certificate_not_after_timestamp_seconds{{{K}}} - certmanager_certificate_not_before_timestamp_seconds{{{K}}}))",
     dict(PCT, custom__cellOptions=GAUGE, thresholds=th([{"value": None, "color": "red"}, {"value": 15, "color": "orange"}, {"value": 30, "color": FINE}]))),
    ("Expires in", f"min by (name) ({EXP} - time())", dict(unit="dtdurations")),
    ("Renews in", f"min by (name) (certmanager_certificate_renewal_timestamp_seconds{{{K}}} - time())", dict(unit="dtdurations"))],
    sort="Lifetime left", desc_sort=False, keywidth=210,
    desc="Time left as a share of each certificate's own lifetime. Three days left is normal for a certificate that lives a week and an emergency for one that lives a year.")
W.table("databases", "Databases", "pod", "Database", [
    ("Up", "max by (pod) (cnpg_collector_up)", dict(mappings=yes("Yes", "No"), custom__cellOptions=TEXT)),
    ("Size", "sum by (pod) (cnpg_pg_database_size_bytes)", dict(unit="bytes", decimals=1)),
    ("Connections", "sum by (pod) (cnpg_backends_total)", {}),
    ("Own backup", "(time() - (max by (pod) (cnpg_collector_last_available_backup_timestamp) > 0)) or (max by (pod) (cnpg_collector_last_available_backup_timestamp) - 1)",
     dict(unit="dtdurations", mappings=[{"type": "value", "options": {"-1": {"text": "none", "index": 0}}}]))],
    desc="Each PostgreSQL server CloudNativePG runs.")
BB = 'job="pi-blackbox"'
W.table("probes", "Reached from outside the cluster", "probe", "Probe", [
    ("Result", f"max by (probe) (probe_success{{{BB}}})", dict(mappings=yes("Answers", "Fails"), custom__cellOptions=TEXT)),
    ("Response", f"max by (probe) (probe_duration_seconds{{{BB}}})", dict(unit="s", **CTEXT((1, "orange"), (2, "red")))),
    ("Certificate expires in", f"min by (probe) (probe_ssl_earliest_cert_expiry{{{BB}}} - time())",
     dict(unit="dtdurations", custom__cellOptions=TEXT, mappings=NODASH, thresholds=th([{"value": None, "color": "red"}, {"value": 129600, "color": "text"}])))],
    info=(f"max by (probe, target) (probe_success{{{BB}}})", {"target": "Target"}), keywidth=130,
    desc="What a Pi on the LAN sees when it reaches each service the way a person would. Response turns orange at one second and red at two, where the alert fires; certificates go red inside the alert's 36 hours.")

# ================================================================== network
UNI = 'job="unifi-api"'


def relabel(expr, label, value, source, regex):
    return f'label_replace({expr}, "{label}", "{value}", "{source}", "{regex}")'


# Each kind of UniFi device has its own UniFi-Poller dashboard, selected by a differently named variable.
# Syslog names a device by its MAC (switches and access points) or by its name with dashes for spaces (the console).
DEVICE = f"unpoller_device_info{{{UNI}}}"
for kind, (uid, var) in {"usw": ("FsfxpWaZz", "Switch"), "uap": ("g5wFWqxZk", "AP"), "udm|ugw|usg|uxg": ("4Yo8IZ-Wk", "Gateway")}.items():
    DEVICE = relabel(relabel(DEVICE, "dash", uid, "type", kind), "dvar", var, "type", kind)
DEVICE = relabel(relabel(DEVICE, "logid", "$1$2$3$4$5$6", "mac", "(..):(..):(..):(..):(..):(..)"), "logname", "$1", "name", "(.*)")
for _ in range(5):
    DEVICE = relabel(DEVICE, "logname", "$1-$2", "logname", "([^ ]+) (.*)")
HIDDEN = ("dash", "dvar", "logid", "logname")
W.table("netdev", "Network devices", "name", "Device", [
    ("Clients", f'sum by (name) (unpoller_device_stations{{{UNI},station_type="user"}})', {}),
    ("CPU", f"100 * max by (name) (unpoller_device_cpu_utilization_ratio{{{UNI}}})", LOADBAR),
    ("Memory", f"100 * max by (name) (unpoller_device_memory_utilization_ratio{{{UNI}}})", LOADBAR),
    ("Temperature", f"max by (name) (unpoller_device_temperature_celsius{{{UNI}}})", dict(unit="celsius", **CTEXT((55, "orange"), (70, "red")))),
    ("Up for", f"max by (name) (unpoller_device_uptime_seconds{{{UNI}}})", dict(unit="dtdurations")),
    ("Update", f"max by (name) (unpoller_device_upgradable{{{UNI}}})", dict(mappings=words(_1=("Available", LOOK), _0=("", None)), custom__cellOptions=TEXT))],
    info=(f'max by (name, type, version, {", ".join(HIDDEN)}) ({DEVICE})', {"type": "Kind", "version": "Firmware", **{h: h for h in HIDDEN}},
          {"type": dict(mappings=words(udm="Gateway", ugw="Gateway", usw="Switch", uap="Access point"), custom__width=110), **{h: dict(custom__hidden=True) for h in HIDDEN}}),
    keywidth=170, desc="The gateway, the switches and the access points, as UniFi reports them. Temperature turns red at 70 °C, where the alert fires. A dash means that kind of device has no such number.")
add_links(W.elements["netdev"], "Device", [to("${__value.text} in UniFi-Poller", "/d/${__data.fields.dash}?var-${__data.fields.dvar}=${__value.raw}&${__url_time_range}"),
                                           to("Logs from ${__value.text}", explore('service.namespace:=unifi (host.id:="${__data.fields.logid}" or service.name:="${__data.fields.logname}")'))])
WAN = f'max by (wan_networkgroup, wan) (label_replace(unpoller_wan_interface_state{{{UNI}}}, "wan", "$1", "wan_interface", "(.*)")) * 0 + 1'


def by_wan(metric, agg="avg"):
    return f"{agg} by (wan) ({metric}{{{UNI}}} * on (wan_networkgroup) group_left (wan) ({WAN}))"


def wan(metric):
    return f'max by (wan) (label_replace({metric}{{{UNI}}}, "wan", "$1", "wan_name", "(.*)"))'


W.table("wan", "Internet links", "wan", "Link", [
    ("State", f'max by (wan) (label_replace(unpoller_wan_interface_state{{{UNI}}}, "wan", "$1", "wan_interface", "(.*)"))', dict(mappings=yes("Active", "Down"), custom__cellOptions=TEXT)),
    ("Latency", by_wan("unpoller_device_wan_monitor_latency_average_seconds"), dict(unit="s", **CTEXT((0.1, LOOK)))),
    ("Monitors answering", by_wan("unpoller_device_wan_monitor_availability_percent", "min"),
     dict(unit="percent", custom__cellOptions=TEXT, thresholds=th([{"value": None, "color": "red"}, {"value": 90, "color": "orange"}, {"value": 99, "color": "text"}]))),
    ("Rated down", f'1000 * {wan("unpoller_wan_provider_download_kbps")}', dict(unit="bps")),
    ("Rated up", f'1000 * {wan("unpoller_wan_provider_upload_kbps")}', dict(unit="bps"))],
    keywidth=150, desc="Each link as the gateway sees it: whether it carries traffic, how long its monitor targets take to answer, and the speed the provider is rated for.")
SL = 'job="starlink-api"'
W.tiles("starlink", "Starlink dish", [
    ("To its point of presence", f"1000 * max(starlink_dish_pop_ping_latency_seconds{{{SL}}})", dict(unit="ms", thresholds=th(QUIET + [{"value": 100, "color": LOOK}]))),
    ("Pings dropped", f"100 * max(avg_over_time(starlink_dish_pop_ping_drop_ratio{{{SL}}}[15m]))", dict(unit="percent", decimals=1, thresholds=th(QUIET + [{"value": 2, "color": LOOK}, {"value": 10, "color": "red"}]))),
    ("Obstructed", f"100 * max(avg_over_time(starlink_dish_currently_obstructed{{{SL}}}[1h]))", dict(unit="percent", decimals=1, thresholds=th(QUIET + [{"value": 1, "color": LOOK}, {"value": 5, "color": "red"}]))),
    ("Down", f"max(starlink_dish_downlink_throughput_bps{{{SL}}})", dict(unit="bps", decimals=1)),
    ("Up", f"max(starlink_dish_uplink_throughput_bps{{{SL}}})", dict(unit="bps", decimals=1)),
    ("Up for", f"max(starlink_dish_uptime_seconds{{{SL}}})", dict(unit="dtdurations"))],
    desc="The dish's own view of its link. Dropped pings are averaged over 15 minutes and obstruction over the hour, as the alerts do; red is where they fire.")
TM = 'job="tmobile-api"'
W.tiles("tmobile", "T-Mobile gateway", [
    ("Network", zero(f'max(tmobile_registration_info{{{TM},registration="registered"}})'), dict(mappings=yes("Registered", "Not registered"), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("Bars", f"max(tmobile_5g_bars{{{TM}}})", dict(thresholds=th([{"value": None, "color": "red"}, {"value": 2, "color": LOOK}, {"value": 3, "color": NEUTRAL}]))),
    ("Signal", f"max(tmobile_5g_rsrp_dbm{{{TM}}})", dict(unit="suffix: dBm")),
    ("Quality", f"max(tmobile_5g_sinr_db{{{TM}}})", dict(unit="dB")),
    ("Up for", f"max(tmobile_uptime_seconds{{{TM}}})", dict(unit="dtdurations"))],
    wide=True, desc="The gateway's own view of its radio link: registration, bars, received signal power and signal quality.")


def radio(metric, extra=""):
    return f'max by (ap_radio) (label_replace(label_join({metric}{{{UNI}{extra}}}, "ap_radio", " · ", "name", "band"), "ap_radio", "$1 GHz", "ap_radio", "(.*)"))'


W.table("wifi", "Wi-Fi radios", "ap_radio", "Radio", [
    ("Channel", radio("unpoller_device_radio_channel"), {}),
    ("Busy", f'100 * {radio("unpoller_device_radio_channel_utilization_total_ratio")}', dict(PCT, custom__cellOptions=GAUGE, thresholds=th([{"value": None, "color": FINE}, {"value": 60, "color": LOOK}, {"value": 80, "color": "orange"}]))),
    ("Clients", radio("unpoller_device_radio_stations", ',station_type="user"'), {})],
    sort="Busy", keywidth=190, desc="How much of each radio's channel is busy, counting the neighbours' traffic too, and who is on it.")

# ================================================================== logs: the newest lines at warning level and above, each led by where it came from
LEVEL = ('| format if (severity_number:range[13, 16]) "warning" as level | format if (severity_number:range[17, 20]) "error" as level'
         ' | format if (severity_number:range[21, 24]) "critical" as level')
FEEDS = {
    "k8s-prod": (f"k8s.cluster.name:=k8s-prod severity_number:>=13 {LEVEL}"
                 ' | format if (service.namespace:=talos) "<k8s.node.name> <service.name>: <_msg>" as _msg'
                 ' | format if (service.namespace:!=talos) "<service.namespace>/<service.name>: <_msg>" as _msg',
                 "Container and Talos node logs at warning level and above, newest first, each line led by its source. Known noisy sources, listed in the logging runbook, appear here too."),
    "network": (f"service.namespace:=unifi severity_number:>=13 {LEVEL}" ' | format "<service.name>: <_msg>" as _msg',
                "UniFi syslog at warning level and above, newest first, each line led by the device. Access points log routine Wi-Fi driver lines as errors.")}
def feed(system):
    query, desc = FEEDS[system]
    P = Panels()
    P.add("feed", "Recent warnings and errors", "logs", [lq(query, "instant", maxLines=200)],
          {"showTime": True, "showLabels": False, "showCommonLabels": False, "wrapLogMessage": False, "prettifyLogMessage": False, "enableLogDetails": True,
           "dedupStrategy": "none", "sortOrder": "Descending"}, {}, desc=desc, links=[to("All of these in Explore", explore(query))])
    return P.elements["feed"]


# Widgets built for one platform or system; a layout names them "<widget>@<platform or system>".
BUILDERS = {"machines": machines, "machine-cpu": machine_cpu_trend, "ssd-drives": ssd_drives, "feed": feed}

# ================================================================== power
# The Pis read their UPSs with NUT and Athena reads its own over SNMP, under different names; U() merges the two shapes per UPS.
def U(nut, snmp):
    return f'(max by (ups) ({nut}) or max by (ups) (label_set({snmp}, "ups", "network-rack")))'


U_BATTERY = U('network_ups_tools_ups_status{flag="OB"}', 'label_match(upsInfoStatus, "upsInfoStatus", "(.* )?OB( .*)?") or upsInfoStatus * 0')
U_CHARGE = U("network_ups_tools_battery_charge", "upsBatteryChargeValue")
U_LOAD = U("network_ups_tools_ups_load", "upsInfoLoadValue")
U_RUNTIME = U("network_ups_tools_battery_runtime", "upsBatteryRuntimeValue")
U_INPUT = U("network_ups_tools_input_voltage", "upsInputVoltageValue")
UPS_NAMES = ["compute-rack", "office", "network-rack"]
UPS_LIST = 'label_join(network_ups_tools_device_info, "lbl", " · ", "ups", "model") or label_join(label_set(upsInfoModel, "ups", "network-rack"), "lbl", " · ", "ups", "upsInfoModel")'
W.tiles("ups", "UPS · ${ups:text}", [
    ("Power from", match(U_BATTERY, "ups", "$ups"), dict(mappings=words(_0=("Mains", None), _1=("Battery", None)), thresholds=th(QUIET + [{"value": 1, "color": "red"}]))),
    ("Charge", match(U_CHARGE, "ups", "$ups"), dict(unit="percent", thresholds=th([{"value": None, "color": "red"}, {"value": 30, "color": "orange"}, {"value": 60, "color": NEUTRAL}]))),
    ("Load", match(U_LOAD, "ups", "$ups"), dict(unit="percent", thresholds=th(QUIET + [{"value": 80, "color": LOOK}, {"value": 90, "color": "orange"}]))),
    ("Runtime", f'{match(U_RUNTIME, "ups", "$ups")} / 60', dict(unit="suffix: min", thresholds=th([{"value": None, "color": "red"}, {"value": 5, "color": "orange"}, {"value": 10, "color": NEUTRAL}]))),
    ("Input", match(U_INPUT, "ups", "$ups"), dict(unit="volt"))],
    desc="Where the UPS is drawing power from, how full its battery is, how much of its capacity is in use, and how long the battery would last at this load. Runtime turns red under five minutes, where the alert fires on battery.")
W.trend("ups-load", "UPS load", [("{{ups}}", U_LOAD)], "percent", hi=100, names=UPS_NAMES, desc="How much of each UPS's capacity is in use.")
W.trend("ups-runtime", "UPS runtime at this load", [("{{ups}}", f"{U_RUNTIME} / 60")], "m", names=UPS_NAMES, desc="How long each battery would last at the load of the moment.")

# ================================================================== devices
KVM = 'job="glkvm-kvmd"'
W.tiles("glkvm", "Recovery console · glkvm", [
    ("Reachable", zero(f"max(up{{{KVM}}})"), dict(mappings=yes("Yes", "No"), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("CPU", f"max(pikvm_hw_cpu_percent{{{KVM}}})", dict(unit="percent", thresholds=TILE_LOAD)),
    ("Memory", f"max(pikvm_hw_mem_percent{{{KVM}}})", dict(unit="percent", thresholds=th(QUIET + [{"value": 80, "color": LOOK}, {"value": 90, "color": "orange"}]))),
    ("Temperature", f"max(pikvm_hw_temp_cpu{{{KVM}}})", dict(unit="celsius")),
    ("To its limit", f"95 - max(pikvm_hw_temp_cpu{{{KVM}}})", dict(unit="celsius", thresholds=ROOM_TILE))],
    desc="The GL-RM1PE console that reaches the machines when nothing else can. Its limit is the 95 °C alert line; the chip itself trips at 115 °C.")

# ================================================================== variables
UPS_RX = '/(?=.*lbl="(?<text>[^"]+)")(?=.*ups="(?<value>[^"]+)")/'
VARS = [qvar("gpu", "query_result(nvidia_smi_gpu_info)", '/instance="([^"]+)"/', hide="hideVariable", multi=True, all_=True, current=ALL),
        qvar("model", "query_result(tensorfold:kv_cache_usage_ratio)", '/model="([^"]+)"/', hide="hideVariable"),
        qvar("ups", f"query_result({UPS_LIST})", UPS_RX, hide="hideVariable", multi=True, all_=True, current=ALL),
        qvar("probe", f"query_result(max by (probe) (probe_success{{{BB}}}))", '/probe="([^"]+)"/', hide="hideVariable", multi=True, all_=True, current=ALL)]
