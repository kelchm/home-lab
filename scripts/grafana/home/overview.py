"""The overview: only what must be seen, across every system.

Each rule below belongs to one class of thing and judges it by that class's own line, so nothing is ranked against a
different kind of thing. A rule returns a row only while its line is crossed: alerts that are firing, and quieter
things worth a look before they become alerts.
"""
from . import widgets as w
from .schema import FIRE, NEUTRAL, NOALERT, QUIET, LOOK, BROKEN, PROBLEM, TEXT, RED, Panels, T, by_name, fixed, ls, match, q, th, union, yes, zero
from .widgets import FS, HOSTS, K, SYS, SYSTEMS

KEYS = "system, thing, what, detail"


# say(), r(), sys(), avail, size and dur are defined once in the WITH prefix every query starts with (see Q),
# because VictoriaMetrics rejects queries over 16,384 characters.
def say(expr, fmt):
    return f'say({expr}, "{fmt}")'


def at(expr, system):
    return f'label_set({expr}, "system", "{system}")'


def rule(expr, what, level, thing=()):
    """A row for each series of expr: what it is, where (the labels in thing), and its level: 3 critical, 2 warning, 1 worth a look."""
    e = f'label_join({expr}, "thing", " ", {", ".join(chr(34) + t + chr(34) for t in thing)})' if thing else expr
    if what:
        e = f'label_set({e}, "what", "{what}")'
    return f"(r({e}) * 0 + {level})"


# ------------------------------------------------------------------ every system: alerts, silence
ALERT = f'({say("(dur >= 3600) / 3600", "alert · firing for $1 h")}) or ({say("(dur < 3600) / 60", "alert · firing for $1 min")})'
# What an alert is about: its machine, or the device, UPS, probe or link it names, or the drive on a machine.
ALERT = f'label_replace(label_replace({ALERT}, "what", "$1", "alertname", "(.*)"), "thing", "$1", "instance", "([a-z][a-z0-9-]*)")'
for lab in ("name", "ups", "probe", "wan_interface"):
    ALERT = f'label_replace({ALERT}, "thing", "$1", "{lab}", "(.+)")'
ALERT = f'label_replace(label_join({ALERT}, "td", " ", "instance", "device"), "thing", "$1", "td", "([a-z][a-z0-9-]* .+)")'
R = [rule(match("alert", "severity", "critical"), None, 3), rule(match("alert", "severity", "warning"), None, 2), rule('label_mismatch(alert, "severity", "critical|warning")', None, 1),
     rule(say("count by (system) (sys(up) == 0)", "$1 affected"), "Collectors silent", 2)]

# ------------------------------------------------------------------ anything that fills up
SECS = "(avail / (-deriv(avail[7d]) > 0))"
USED = "100 * (1 - avail / (size > 0))"
FULL_IN = "full in $1 days at the pace of the last week"
R += [rule(f'sys({say(f"({SECS} < 604800) / 86400", FULL_IN)})', "Disk filling up", 2, ("instance", "mountpoint")),
      rule(f'sys({say(f"(({SECS} >= 604800) < 2592000) / 86400", FULL_IN)})', "Disk filling up", 1, ("instance", "mountpoint")),
      rule(f'sys({say(f"({USED}) >= 85", "$1% used")})', "Disk nearly full", 2, ("instance", "mountpoint"))]


# ------------------------------------------------------------------ machines and devices: degrees to each one's own limit
def sens(label, metric="node_hwmon_temp_celsius"):
    return f'max by (instance, platform) ({metric} * on (instance, chip, sensor) group_left node_hwmon_sensor_label{{label="{label}"}})'


def chip(name):
    return f'max by (instance, platform) (node_hwmon_temp_celsius * on (instance, chip) group_left node_hwmon_chip_names{{chip_name="{name}"}})'


# Assumed limits where a machine publishes none: 95 °C for the AMD CPUs, 85 °C for a Pi 5, and the alert lines for the KVM and the UniFi devices.
ROOM = (f'(95 - {sens("Tctl")}) or ({sens("Package id 0", "node_hwmon_temp_crit_celsius")} - {sens("Package id 0")}) or max by (instance, platform) (nvidia_smi_temperature_gpu_tlimit)'
        f' or (95 - {chip("k10temp")}) or (85 - {chip("cpu_thermal")}) or (95 - max by (instance, platform) (pikvm_hw_temp_cpu))'
        ' or (70 - max by (instance, platform) (label_replace(unpoller_device_temperature_celsius, "instance", "$1", "name", "(.*)")))')
SECTORS = 'sum by (platform, instance, device) ({__name__=~"drive:ata_(reallocated|pending|uncorrectable)_sectors"})'
R += [rule(f'sys({say("room < 5", "$1 °C from its limit")})', "Running hot", 2, ("instance",)),
      rule(f'sys({say("(room >= 5) < 15", "$1 °C from its limit")})', "Running hot", 1, ("instance",)),
      rule("sys(max by (instance, platform) (nvidia_smi_clocks_event_reasons_hw_thermal_slowdown) == 1)", "GPU slowed by heat", 2, ("instance",)),
      rule("sys(drive:health_status == 0)", "Drive reports failure", 3, ("instance", "device")),
      rule("sys(drive:health_status == 2)", "Drive reports a warning", 2, ("instance", "device")),
      rule(f'sys({say("max by (instance, platform, device) (smartctl_device_percentage_used) >= 80", "$1% worn")})', "Drive wearing out", 1, ("instance", "device")),
      rule(f'sys({say(f"{SECTORS} > 0", "$1 bad sectors")})', "Disk has bad sectors", 1, ("instance", "device")),
      rule('sys(label_replace(diskStatus == 3, "device", "$1", "diskID", "(.*)"))', "Disk not in use", 1, ("instance", "device"))]

# ------------------------------------------------------------------ Kubernetes cluster
NOT_READY = (f'sum(kube_pod_status_ready{{condition="false",{K}}} * on (namespace, pod) group_left kube_pod_status_phase{{phase="Running",{K}}})'
             f' + (count(kube_pod_status_phase{{phase=~"Pending|Failed|Unknown",{K}}} == 1) or vector(0))')


def k8s(expr, fmt, what, level):
    """A count has to stay above zero for ten minutes before it is listed, as an alert rule's "for" would require."""
    return rule(at(say(f"min_over_time(({expr})[10m:1m]) > 0", fmt), "k8s-prod"), what, level)


RENEWAL = (f"((certmanager_certificate_expiration_timestamp_seconds{{{K}}} - time()) / 3600)"
           f" and on (name, exported_namespace) ((certmanager_certificate_renewal_timestamp_seconds{{{K}}} - time()) < -3600)")
R += [k8s(f'sum(kube_node_status_condition{{condition="Ready",status!="true",{K}}})', "$1 affected", "Nodes not ready", 3),
      k8s(NOT_READY, "$1 affected", "Pods not ready", 2),
      k8s(f'count(flux_resource_info{{ready!="True",suspended!="True",{K}}})', "$1 affected", "Deploys failing", 2),
      k8s(f'sum(longhorn_volume_robustness{{state="faulted",{K}}})', "$1 affected", "Volumes faulted", 3),
      k8s(f'sum(longhorn_volume_robustness{{state="degraded",{K}}})', "$1 affected", "Volumes degraded", 2),
      rule(at(say(RENEWAL, "renewal overdue · expires in $1 h"), "k8s-prod"), "Certificate not renewing", 2, ("name",))]

# ------------------------------------------------------------------ Proxmox cluster
R += [rule(at('max(label_value(pve_cluster_info, "quorate")) == 0', "pve-sbx"), "Cluster lost quorum", 3),
      rule(at('label_replace(max by (id) (pve_up{id=~"node/.*"}) == 0, "node", "$1", "id", "node/(.*)")', "pve-sbx"), "Node offline", 3, ("node",)),
      rule(at(f"max by (name) (max by (id) (pve_not_backed_up_info) * on (id) group_left(name) {w.GI})", "pve-sbx"), "Guest is in no backup job", 1, ("name",))]

# ------------------------------------------------------------------ power, network and the services seen from outside: the quieter lines before the alerts'
UNI, BB = w.UNI, w.BB
R += [rule(at(say(f"({w.U_RUNTIME} < 600) / 60", "$1 min of battery at this load"), "power"), "UPS runtime short", 1, ("ups",)),
      rule(at(say(f"{w.U_LOAD} >= 80", "$1% of its capacity"), "power"), "UPS heavily loaded", 1, ("ups",)),
      rule(at(say(f"100 * max by (name) (unpoller_device_cpu_utilization_ratio{{{UNI}}}) >= 80", "$1% CPU"), "network"), "Network device busy", 1, ("name",)),
      rule(at(say(f'100 * {w.radio("unpoller_device_radio_channel_utilization_total_ratio")} >= 60', "channel $1% busy"), "network"), "Wi-Fi channel busy", 1, ("ap_radio",)),
      rule(at(say(f'1000 * {w.by_wan("unpoller_device_wan_monitor_latency_average_seconds")} > 100', "$1 ms to its monitors"), "network"), "Internet link slow", 1, ("wan",)),
      rule(at(say("100 * max by (instance) (avg_over_time(starlink_dish_currently_obstructed[1h])) >= 1", "obstructed $1% of the last hour"), "network"), "Dish obstructed", 1, ("instance",)),
      rule(at(say("100 * max by (instance) (avg_over_time(starlink_dish_pop_ping_drop_ratio[15m])) >= 2", "$1% of pings dropped"), "network"), "Dish dropping pings", 1, ("instance",)),
      rule(at(say("max by (instance) (tmobile_5g_bars) <= 1", "$1 bars"), "network"), "Weak 5G signal", 1, ("instance",)),
      rule(f'sys({say(f"1000 * max by (probe, job) (probe_duration_seconds{{{BB}}}) > 1000", "$1 ms to answer")})', "Slow from outside the cluster", 1, ("probe",))]

FSEL = f"{HOSTS},{FS}"
TEMPLATES = (f'{SYS}, say(x, f) = label_replace(count_values without () ("n", round(x)), "detail", f, "n", "(.*)"), r(x) = max by ({KEYS}) (x), '
             f"avail = node_filesystem_avail_bytes{{{FSEL}}}, size = node_filesystem_size_bytes{{{FSEL}}}, dur = sys({FIRE})")


def Q(body):
    """A query over the rules: seen holds every row whose line is crossed."""
    return f"WITH ({TEMPLATES}, alert = ({ALERT}), room = ({ROOM}), seen = ({union(*R)})) {body}"


def tagged(expr):
    """Adds the order systems are listed in, and the address of each system's tab."""
    for i, c in enumerate(SYSTEMS + ["unassigned"]):
        expr = f'label_replace({expr}, "order", "{i}", "system", "{c}")'
    for c in SYSTEMS:
        expr = f'label_replace({expr}, "tab", "{w.tab_slug(c)}", "system", "{c}")'
    return expr


# Every system gets a row even when it has nothing to say.
BASE = union(*[ls("vector(0)", system=c) for c in SYSTEMS])


def ordered(expr):
    return f'sort_by_label({tagged(f"({expr}) or on (system) ({BASE})")}, "order")'


LEVEL = [{"type": "value", "options": {"3": {"text": "Critical", "color": "red", "index": 0}, "2": {"text": "Warning", "color": "orange", "index": 1}, "1": {"text": "Worth a look", "color": LOOK, "index": 2}}}]
NOW = [{"type": "value", "options": {**LEVEL[0]["options"], "0": {"text": "All good", "color": "#8E96A3", "index": 3}, "-1": {"text": "Not collected", "color": "#5C6370", "index": 4}}}]
CELL = [{"type": "value", "options": {"3": {"text": "critical", "index": 0}, "2": {"text": "warning", "index": 1}, "1": {"text": "worth a look", "index": 2}, "0": {"text": "nothing firing", "index": 3}}}]
RAMP = th([{"value": None, "color": "#1D2025"}, {"value": 0, "color": "#2E333B"}, {"value": 1, "color": LOOK}, {"value": 2, "color": "orange"}, {"value": 3, "color": "red"}])
TDEF = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "minWidth": 64}, "noValue": "", "color": {"mode": "thresholds"}, "thresholds": th([{"value": None, "color": "text"}])}

E = Panels()
# ------------------------------------------------------------------ the board: one row per system
E.add("board", "Systems", "table", [q(Q(ordered("topk by (system) (1, seen)")), fmt="table")], {"cellHeight": "sm", "showHeader": False}, TDEF,
      [by_name("tab", custom__hidden=True), by_name("System", custom__width=92), by_name("Now", custom__width=118, custom__cellOptions=TEXT, mappings=NOW)],
      desc="One row for every system, saying the worst thing true of it right now in words. Click a system to open its tab.",
      transformations=[T("sortBy", fields={}, sort=[{"field": "order"}]),
                       T("organize", excludeByName={"Time": True, "order": True, "detail": True}, indexByName={"system": 0, "Value": 1, "what": 2, "thing": 3},
                         renameByName={"system": "System", "Value": "Now", "what": "What", "thing": "Where"})])
A = f'sys(ALERTS{{alertstate="firing",{NOALERT}}})'
ALEVEL = f'max by (system) ((label_match({A}, "severity", "critical") * 0 + 3) or (label_match({A}, "severity", "warning") * 0 + 2) or ({A} * 0 + 1))'
E.add("strip", "Alerts over the last 7 days", "state-timeline", [q(w.QS(ordered(f"max_over_time(({ALEVEL})[30m:5m])")), "{{system}}", instant=False, interval="30m")],
      {"alignValue": "left", "legend": {"showLegend": False}, "mergeValues": True, "rowHeight": 0.62, "showValue": "never", "tooltip": {"mode": "single"}},
      {"color": {"mode": "thresholds"}, "thresholds": RAMP, "custom": {"fillOpacity": 100, "lineWidth": 0, "spanNulls": False}, "mappings": CELL,
       "links": [{"title": "Open alert statistics", "url": "/d/ehXxUsGSk?${__url_time_range}"}]},
      desc="The worst alert firing on each system over the last week. Grey means nothing was firing.", qopts={"timeFrom": "7d", "hideTimeOverride": True})

# ------------------------------------------------------------------ the list
def needs_attention(system=None):
    """What has crossed its line, for the whole lab or one system: a list, and a line shown in its place while there is nothing to list."""
    seen = match("seen", "system", system) if system else "seen"
    where = f"on {system}" if system else "in the lab"
    P = Panels()
    P.add("attention", "Needs attention", "table", [q(Q(tagged(seen)), fmt="table")], {"cellHeight": "sm", "showHeader": True, "sortBy": [{"displayName": "Level", "desc": True}]}, TDEF,
          [by_name("Level", custom__width=108, custom__cellOptions=TEXT, mappings=LEVEL), by_name("System", custom__width=88), by_name("tab", custom__hidden=True)],
          desc=f"Everything {where} that has crossed its own line: alerts that are firing, and quieter things worth a look before they become alerts. Each kind of thing is judged by its own rule.",
          transformations=[T("organize", excludeByName={"Time": True, "order": True, **({"system": True} if system else {})},
                             indexByName={"Value": 0, "what": 1, "system": 2, "thing": 3, "detail": 4},
                             renameByName={"Value": "Level", "what": "What", "system": "System", "thing": "Where", "detail": "Detail"})])
    P.add("quiet", "Needs attention", "stat", [q(Q(seen))], {"colorMode": "none", "graphMode": "none", "justifyMode": "center", "orientation": "auto", "reduceOptions": RED, "textMode": "value",
                                                          "text": {"valueSize": 18}}, {"noValue": "Nothing needs attention", "color": fixed("text")},
          desc=f"Shown while nothing {where} has crossed its line.")
    return P.elements

# ------------------------------------------------------------------ the services, seen from outside the cluster
REACH = f"(1000 * max by (probe) (probe_duration_seconds{{{BB}}}) and on (probe) (max by (probe) (probe_success{{{BB}}}) == 1)) or (max by (probe) (probe_success{{{BB}}}) - 1)"
E.add("reach", "Services, from outside the cluster", "stat", [q(REACH, "{{probe}}")],
      {"colorMode": "background_solid", "graphMode": "none", "justifyMode": "center", "orientation": "vertical", "reduceOptions": RED, "textMode": "value_and_name", "wideLayout": False,
       "text": {"titleSize": 12, "valueSize": 17}},
      {"unit": "ms", "decimals": 0, "color": {"mode": "thresholds"}, "thresholds": th(QUIET + [{"value": 1000, "color": LOOK}, {"value": 2000, "color": "orange"}]),
       "mappings": [{"type": "value", "options": {"-1": {"text": "Down", "color": "red", "index": 0}}}], "links": [{"title": "Open the probes", "url": "/d/pi-blackbox?${__url_time_range}"}]},
      desc="How long each service takes to answer a Pi on the LAN that reaches it the way a person would. Yellow from one second, orange from two, where the alert fires; red when it does not answer.")

# ------------------------------------------------------------------ the watch itself
LOG_LINES = 'sum(rate(vl_rows_ingested_total{cluster="k8s-prod",job="victoria-logs-single-server",type="opentelemetry_protobuf"}[5m]))'
E.tiles("watch", "The watch itself", [
    ("Sources reporting", "count(up == 1)", {}),
    ("Sources silent", zero("count(up == 0)"), dict(thresholds=PROBLEM)),
    ("Log lines per second", zero(LOG_LINES), dict(thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("Alert delivery", zero('count(ALERTS{alertname="Watchdog",alertstate="firing"})'), dict(mappings=yes("Alive", "Silent"), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("Alert rules loaded", "count(vmalert_alerting_rules_last_evaluation_samples)", {}),
    ("Alert rules failing", zero("count(increase(vmalert_alerting_rules_errors_total[1h]) > 0)"), dict(thresholds=BROKEN))],
    desc="An empty list only means something if the monitoring is alive. These show that metrics and logs are arriving, that alerts can be delivered, and that the rules are being evaluated.")
E.elements["watch"]["spec"]["vizConfig"]["spec"]["fieldConfig"]["defaults"]["links"] = [{"title": "Open the monitoring pipeline", "url": "/d/observability-pipeline?${__url_time_range}"}]

# ------------------------------------------------------------------ the inventory: every kind of thing in every system
ORDER = ["Machines", "GPUs", "Drives", "Filesystems", "Storage pools", "Volumes", "Guests", "Namespaces", "Models", "Certificates", "Routes", "Databases",
         "Service probes", "Network devices", "Wi-Fi radios", "Internet links", "WAN devices", "UPSs", "Consoles"]


def cls(name, expr, *labels):
    """The kind carries its place in ORDER as a prefix, so the joined table can be sorted on it; a value mapping hides the prefix."""
    return (f'label_set(max by (thing) (label_join({expr}, "thing", " ", {", ".join(chr(34) + l + chr(34) for l in labels)})) * 0, '
            f'"kind", "{ORDER.index(name):02d} {name}", "o", "{ORDER.index(name):02d}")')


def fsys(platform):
    return f'node_filesystem_size_bytes{{{HOSTS},platform="{platform}",{FS}}}'


def machines(job):
    return f'node_uname_info{{job="{job}"}}'


def drives(platform):
    return f'drive:info{{platform="{platform}"}}'


THINGS = {
    "k8s-prod": [cls("Machines", f'node_uname_info{{job="node-exporter",{K}}}', "instance"), cls("Drives", drives("k8s"), "instance", "device"), cls("Filesystems", fsys("k8s"), "instance", "mountpoint"),
                 cls("Volumes", f'longhorn_volume_robustness{{state="healthy",{K}}}', "pvc_namespace", "pvc"), cls("Namespaces", f'kube_namespace_status_phase{{phase="Active",{K}}}', "namespace"),
                 cls("Certificates", f'certmanager_certificate_ready_status{{condition="True",{K}}}', "name"), cls("Routes", w.REQ, "svc"), cls("Databases", "cnpg_collector_up", "pod"),
                 cls("Service probes", f'probe_success{{{BB},probe!="unifi-gateway"}}', "probe")],
    "pve-sbx": [cls("Machines", machines("pve-node"), "instance"), cls("Drives", drives("pve"), "instance", "device"), cls("Filesystems", fsys("pve"), "instance", "mountpoint"),
                cls("Guests", w.GI, "name"), cls("Storage pools", "pve_storage_info", "storage")],
    "spark": [cls("Machines", machines("spark-node"), "instance"), cls("Drives", drives("spark"), "instance", "device"), cls("Filesystems", fsys("spark"), "instance", "mountpoint"),
              cls("GPUs", "nvidia_smi_gpu_info", "instance"), cls("Models", "tensorfold:kv_cache_usage_ratio", "model")],
    "athena": [cls("Machines", machines("synology-node"), "instance"), cls("Drives", drives("synology"), "instance", "device"), cls("Filesystems", fsys("synology"), "instance", "mountpoint"),
               cls("Storage pools", f'raidStatus{{{w.SYN},raidName=~"Storage Pool.*"}}', "raidName"), cls("Volumes", f'raidStatus{{{w.SYN},raidName=~"Volume.*"}}', "raidName")],
    "network": [cls("Network devices", f"unpoller_device_info{{{UNI}}}", "name"), cls("Wi-Fi radios", w.radio("unpoller_device_radio_channel"), "ap_radio"),
                cls("Internet links", f"unpoller_wan_interface_state{{{UNI}}}", "wan_interface"), cls("WAN devices", "starlink_dish_info or tmobile_device_info", "instance"),
                cls("Service probes", f'probe_success{{{BB},probe="unifi-gateway"}}', "probe")],
    "power": [cls("UPSs", w.UPS_LIST, "ups")],
    "devices": [cls("Machines", machines("pi-node"), "instance"), cls("Filesystems", fsys("pi"), "instance", "mountpoint"), cls("Consoles", 'up{job="glkvm-kvmd"}', "instance")]}


def inventory(c, things):
    """One series per kind of thing, with a text label saying how many there are, or how many of them are off."""
    col = c.replace("-", "_")
    body = (f'WITH (t = ({union(*things)}), tot = count by (kind, o) (t), off = count by (kind, o) ((t + on (thing) group_left () (max by (thing) ({match("seen", "system", c)}))) > 0)) '
            f'sort_by_label((label_join(count_values without () ("a", tot) * on (kind) group_left (b) count_values without () ("b", off), "{col}", " of ", "b", "a")) '
            f'or on (kind) label_replace(count_values without () ("a", tot), "{col}", "$1", "a", "(.*)"), "o")')
    return q(Q(body), ref="ABCDEFG"[list(THINGS).index(c)], fmt="table")


OFF = [{"type": "regex", "options": {"pattern": ".* of .*", "result": {"color": LOOK, "index": 0}}}]
DROP = {"Time": True, **{f"{f} {i}": True for f in ("Time", "a", "b", "o") for i in range(1, 9)}, **{f"Value #{r}": True for r in "ABCDEFG"}, "a": True, "b": True, "o": True}
E.add("inventory", "Everything that is watched", "table", [inventory(c, t) for c, t in THINGS.items()], {"cellHeight": "sm", "showHeader": True}, dict(TDEF, noValue="–"),
      [by_name("Kind", custom__width=150, mappings=[{"type": "regex", "options": {"pattern": "^\\d+ (.*)$", "result": {"text": "$1", "index": 0}}}])]
      + [by_name(c, custom__cellOptions=TEXT, mappings=OFF) for c in THINGS],
      desc="Every kind of thing in every system and how many of each are being watched. A plain number means all of them are fine. Coloured text such as 1 of 4 means that many have crossed their line. "
           "A dash means that kind of thing is not part of that system.",
      transformations=[T("joinByField", byField="kind", mode="outerTabular"), T("sortBy", fields={}, sort=[{"field": "kind"}]),
                       T("organize", excludeByName=DROP, indexByName={"kind": 0, **{c.replace("-", "_"): i + 1 for i, c in enumerate(THINGS)}},
                         renameByName={"kind": "Kind", **{c.replace("-", "_"): c for c in THINGS}})])
