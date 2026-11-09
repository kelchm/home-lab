"""Home · E-paper: the whole lab on one 1600×1200 page in four greys.

Inker captures the dashboard in kiosk mode with the light theme and passes the screenshot through in grey (its preview
mode; einkPreview would dither it to one bit). So the page uses four flat shades and no colour: black for words, numbers
and data; dark grey for secondary words, rules and warnings; light grey for chart fills, what is left of a capacity and
lesser alerts; and the page's white. Every panel is transparent. Antialiasing adds in-between shades at edges. Preview
mode bypasses Inker's one-bit processing and its 90 kB downscaling limit. Thirty grid rows fill the page without scrolling.

One query, `systems`, runs Home's own health rules (overview.Q) and returns every row the page needs about health: a
level for each system, the headline, and the list of what needs attention. The other panels read it through the
Dashboard datasource, so the page judges health exactly as Home does.
"""
from . import overview as e
from . import widgets as w
from .schema import Panels, T, by_name, by_ref, dashboard, datasource_variable, fixed, ls, match, q, ref, th, union
from .widgets import BB, HOSTS, K, SYN, UNI

INK = "#000000"
DARK = "#555555"
LIGHT = "#BBBBBB"   # brightness 187 exceeds Grafana's 180 cutoff for dark text on a solid background
PAGE = "#FAFAFA"   # the light theme's page: a state drawn in it disappears into the white


def tint(shade):
    """The fill opacity that turns a black series into this shade on the page, so a chart keeps its black line."""
    return round(100 * (1 - int(shade[1:3], 16) / 255))


LAST = {"calcs": ["lastNotNull"], "fields": "", "values": False}
DAY = {"timeFrom": "24h", "hideTimeOverride": True, "maxDataPoints": 300, "interval": "5m"}
WEEK = {"timeFrom": "7d", "hideTimeOverride": True, "maxDataPoints": 400, "interval": "30m"}
# Home's levels, darker as they get worse: worth a look on light grey, a warning white on dark grey, a critical white on black.
SEVERITY = [{"value": 1, "color": LIGHT}, {"value": 2, "color": DARK}, {"value": 3, "color": INK}]
LOUD = th([{"value": None, "color": "transparent"}, *SEVERITY])
INSET = 12   # where a stat's or a chart's own text starts inside its panel

P = Panels()


# ------------------------------------------------------------------ panel kinds
def bare(key):
    P.elements[key]["spec"]["transparent"] = True
    return key


def shape(kind, name, config, left=None, width=None, right=None, top=0, height=16, background="transparent"):
    """One element of a canvas, placed from the panel's content box, 9 px inside the panel's top-left corner, and held to
    the left edge, the right edge, or both, which stretches it."""
    edges = {k: v for k, v in (("left", left), ("right", right)) if v is not None}
    placement = {**edges, "top": top, "height": height, **({} if len(edges) == 2 else {"width": width})}
    return {"type": kind, "name": name, "config": config, "background": {"color": {"fixed": background}}, "border": {"color": {"fixed": "transparent"}, "width": 0, "radius": 0},
            "placement": placement, "constraint": {"horizontal": "leftright" if len(edges) == 2 else next(iter(edges)), "vertical": "top"}}


def words_at(text, size, shade=INK):
    return {"text": {"mode": "fixed", "fixed": text}, "color": {"fixed": shade}, "size": size, "align": "left", "valign": "middle"}


def heading(key, title, title_px, aside="", aside_px=0, queries=(), right=None, **defaults):
    """A section's name in black capitals, a dark grey word after it, then a heavy dark grey rule to the end of the column.
    A canvas, because a text panel one row high has only 12 px inside it. Canvas cannot measure text, so each word carries
    its width in Inter at that size, measured in the browser. right is a field shown at the far end, such as the time."""
    els = [shape("text", "title", words_at(title.upper(), 15), INSET, width=title_px + 4)]
    start, stop = INSET + title_px + 12, 0
    if aside:
        els.append(shape("text", "aside", words_at(aside, 13, DARK), start, width=aside_px + 4))
        start += aside_px + 12
    if right:
        value = {"text": {"mode": "field", "field": right, "fixed": ""}, "color": {"fixed": DARK}, "size": 13, "align": "right", "valign": "middle"}
        els.append(shape("metric-value", "right", value, right=INSET, width=300))
        stop = INSET + 312
    els.append(shape("rectangle", "rule", words_at("", 1), start, right=stop, top=7, height=3, background=DARK))
    opts = {"inlineEditing": False, "showAdvancedTypes": False, "panZoom": False, "infinitePan": False,
            "root": {"type": "frame", "name": "root", "elements": els, "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "transparent"}},
                     "constraint": {"horizontal": "left", "vertical": "top"}, "placement": {}}}
    P.add(key, "", "canvas", list(queries), opts, {"color": fixed(INK), **defaults})
    return bare(key)


def stat(key, queries, size, label=14, unit=None, decimals=0, names=(), **defaults):
    """Numbers set in black type, one per query, side by side, each under its name."""
    d = {"color": fixed(INK), "decimals": decimals, "noValue": "–", **defaults, **({"unit": unit} if unit else {})}
    opts = {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "vertical", "wideLayout": False, "textMode": "value_and_name",
            "showPercentChange": False, "reduceOptions": LAST, "text": {"titleSize": label, "valueSize": size}}
    P.add(key, "", "stat", queries, opts, d, list(names))
    return bare(key)


def words(key, queries, size, text, transformations, mappings, label=None, justify="auto", **defaults):
    """Text on a background as dark as its level (see SEVERITY). The mappings match exact values only: a range would also catch a
    line such as "2 things need attention", which Grafana reads as the number 2."""
    P.add(key, "", "stat", queries,
          {"colorMode": "background_solid", "graphMode": "none", "justifyMode": justify, "orientation": "horizontal", "wideLayout": True, "textMode": text, "showPercentChange": False,
           "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}, "text": {"titleSize": label or size, "valueSize": size}},
          {"color": {"mode": "thresholds"}, "thresholds": LOUD, "mappings": [{"type": "value", "options": {k: {"text": v, "index": i} for i, (k, v) in enumerate(mappings.items())}}],
           **defaults}, transformations=transformations)
    return bare(key)


def chart(key, title, queries, unit, overrides=(), hi=None, fill=tint(LIGHT), width=2, **custom):
    """The last day as a black line over a grey fill: no axes, since the number above it gives the scale, and a dark grey line at
    zero to stand on, so the data is the only black in the chart."""
    c = {"drawStyle": "line", "lineInterpolation": "linear", "lineWidth": width, "fillOpacity": fill, "gradientMode": "none", "showPoints": "never", "spanNulls": 600000,
         "axisBorderShow": False, "axisGridShow": False, "axisPlacement": "hidden", "axisSoftMin": 0, "stacking": {"mode": "none", "group": "A"}, "thresholdsStyle": {"mode": "line"},
         **custom}
    d = {"unit": unit, "decimals": 0, "color": fixed(INK), "custom": c, "thresholds": th([{"value": None, "color": "transparent"}, {"value": 0, "color": DARK}]),
         **({"max": hi} if hi is not None else {})}
    time_axis = {"matcher": {"id": "byType", "options": "time"}, "properties": [{"id": "custom.axisPlacement", "value": "hidden"}]}
    P.add(key, title, "timeseries", queries, {"legend": {"showLegend": False, "displayMode": "list", "placement": "bottom", "calcs": []}, "tooltip": {"mode": "none"}}, d,
          [time_axis, *overrides], qopts=DAY)
    return bare(key)


def bars(key, queries, transformations, overrides=(), stacking="none", width=0.4, **defaults):
    """Solid black bars, one per thing, each named by its label and carrying no scale."""
    opts = {"orientation": "horizontal", "xField": "bar", "stacking": stacking, "showValue": "never", "groupWidth": width, "barWidth": width, "barRadius": 0, "fullHighlight": False,
            "xTickLabelRotation": 0, "xTickLabelSpacing": 0, "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom", "calcs": []}, "tooltip": {"mode": "none"}}
    d = {"color": fixed(INK), "custom": {"lineWidth": 1, "fillOpacity": 100, "gradientMode": "none", "axisPlacement": "auto", "axisGridShow": False, "axisBorderShow": False,
                                         "axisSoftMin": 0}, **defaults}
    scale = {"matcher": {"id": "byType", "options": "number"}, "properties": [{"id": "custom.axisPlacement", "value": "hidden"}]}
    P.add(key, "", "barchart", queries, opts, d, [scale, *overrides], transformations=transformations)
    return bare(key)


def labelled(expr, by, fmt):
    """One series per `by`, labelled with its name and rounded value, "office · 71 min": a bar that carries its own number has
    nothing drawn past its end for the edge of the panel to cut off."""
    named = f'label_join(label_replace(count_values without () ("v", round({expr})), "v", "{fmt}", "v", "(.*)"), "bar", " · ", "{by}", "v")'
    return f"max by ({by}, bar) ({named} * 0 + on ({by}) group_left () ({expr}))"


def table(expr, ref_id="A"):
    return q(expr, ref=ref_id, fmt="table")


def ranked(key, expr, by, fmt, width):
    """Bars longest first, each named with its value."""
    return bars(key, [table(labelled(expr, by, fmt))], width=width, transformations=[
        T("sortBy", fields={}, sort=[{"field": "Value", "desc": True}]), T("organize", excludeByName={"Time": True, by: True}, indexByName={"bar": 0}, renameByName={})])


def fullness(key, pct, by, width):
    """How full each thing is: black for what is used and light grey for what is left, on one scale to 100%."""
    used = labelled(pct, by, "$1%")
    return bars(key, [table(used, "A"), table(f"100 - {used}", "B")], stacking="normal", width=width, max=100, overrides=[by_name("Value #B", color=fixed(LIGHT))],
                transformations=[T("joinByField", byField="bar", mode="outer"),
                                 T("organize", excludeByName={f"{f} {i}": True for f in ("Time", by) for i in (1, 2)}, indexByName={"bar": 0}, renameByName={})])


def series(expr, legend, ref_id="A"):
    return q(expr, legend, ref_id, instant=False, interval="5m")


# ------------------------------------------------------------------ health: Home's rules, once
BASE = union(*[ls("vector(0)", system=c) for c in w.SYSTEMS])
WARN = 'sys(ALERTS{alertstate="firing",severity=~"critical|warning"})'
HEALTH = (", worst = topk by (system) (1, seen), lvl = max by (system) (seen) or on (system) (" + BASE + "), "
          'said(x, f) = label_replace(count_values without () ("n", round(x)), "line", f, "n", "(.*)"), '
          'item = label_replace(label_replace(label_join(seen, "line", " · ", "what", "thing", "detail"), "line", "$1 · $2", "line", "(.*) ·  · (.*)"), "line", "$1", "line", "(.*) · "), '
          f"recent = topk(1, max by (alertname, system) (tlast_over_time(({WARN})[7d:5m]))), ago = time() - recent")
# Worded as Home's levels: "worth a look" until something reaches a warning.
ONE, MANY = "((count(seen) == 1) * 0 + scalar(max(seen)))", '(count_values without () ("n", count(seen) > 1) * 0 + scalar(max(seen)))'
HEADLINE = union(
    'label_set(vector(0) unless on () count(seen), "line", "All systems normal")',
    f'label_set({ONE} < 2, "line", "1 thing is worth a look")', f'label_set({ONE} >= 2, "line", "1 thing needs attention")',
    f'label_replace({MANY} < 2, "line", "$1 things are worth a look", "n", "(.*)")', f'label_replace({MANY} >= 2, "line", "$1 things need attention", "n", "(.*)")')
# The worst three, worst first; the module headers say which system each is on. While nothing needs attention: how long
# it has been quiet, and what last broke the quiet.
ITEMS = union('topk(3, item)', 'said(count(seen) - 3 > 0, "+$1 more on Home") * 0')
QUIET = union(
    'said(clamp_min((ago < 3600) / 60, 1), "No warnings for $1 min") * 0',
    'said(((ago >= 3600) < 172800) / 3600, "No warnings for $1 h") * 0',
    'said((ago >= 172800) / 86400, "No warnings for $1 days") * 0',
    'label_replace(label_join(recent, "line", " on ", "alertname", "system"), "line", "Last: $1", "line", "(.*)") * 0 - 1',
    'label_set(vector(0) unless on () recent, "line", "No warnings in the last 7 days")')
ROWS = union(ls("lvl", kind="system"), ls(f"({HEADLINE})", kind="headline"), ls(f"({ITEMS})", kind="item"), ls(f"(({QUIET}) unless on () count(seen))", kind="quiet"))
SYSTEMS_QUERY = e.Q("seen").removesuffix(") seen") + HEALTH + ") " + ROWS
LEVELS = {"3": "Critical", "2": "Warning", "1": "Worth a look"}


def only(*kinds, system=None):
    """The rows of `systems` one panel shows."""
    t = [T("filterByValue", type="include", match="any", filters=[{"fieldName": "kind", "config": {"id": "equal", "options": {"value": k}}} for k in kinds])]
    if system:
        t.append(T("filterByValue", type="include", match="all", filters=[{"fieldName": "system", "config": {"id": "equal", "options": {"value": system}}}]))
    return t


def from_systems():
    """Rows of the `systems` panel's query, untransformed; build() fills in its panel id."""
    return {"kind": "PanelQuery", "spec": {"query": {"kind": "DataQuery", "group": "datasource", "version": "v0", "datasource": {"name": "-- Dashboard --"},
                                                     "spec": {"panelId": 0, "withTransforms": False}}, "refId": "A", "hidden": False}}


def state(key, system):
    """A system's state, as Home's board words it."""
    return words(key, [from_systems()], 14, "value", only("system", system=system), {**LEVELS, "0": "Normal"}, justify="center", noValue="No data")


# ------------------------------------------------------------------ masthead and attention
heading("masthead", "Home lab", 79, queries=[q("vector(time() * 1000)", "updated")], right="updated", unit="time:[Updated] HH:mm · dddd D MMMM", noValue="Metrics unavailable")
words("headline", [from_systems()], 24, "value_and_name", only("headline"), {**{k: " " for k in LEVELS}, "0": " "}, label=64, noValue="Health unavailable",
      displayName="${__data.fields.line}")
words("systems", [table(SYSTEMS_QUERY)], 17, "value_and_name", only("item", "quiet") + [T("sortBy", fields={}, sort=[{"field": "Value", "desc": True}])],
      {**LEVELS, "0": " ", "-1": " "}, noValue="Health unavailable", displayName="${__data.fields.line}")
P.elements["systems"]["spec"]["description"] = "What needs attention, judged by Home's own rules, worst first. While nothing does, the most recent warning or critical alert of the last week."

# ------------------------------------------------------------------ Kubernetes
CPU = '100 * (1 - avg(rate(node_cpu_seconds_total{%s,platform="%s",mode="idle"}[5m])))'
MEM = '100 * (1 - sum(node_memory_MemAvailable_bytes{%s,platform="%s"}) / sum(node_memory_MemTotal_bytes{%s,platform="%s"}))'
REQUESTS = f"60 * sum(rate(traefik_service_requests_total{{{K}}}[5m]))"
PCT = [by_name("CPU", unit="percent"), by_name("memory", unit="percent")]


def cpu(p):
    return CPU % (HOSTS, p)


def mem(p):
    return MEM % (HOSTS, p, HOSTS, p)


heading("k8s-head", "Kubernetes", 99, "k8s-prod", 57)
state("k8s-state", "k8s-prod")
stat("k8s-hero", [q(f'count(kube_pod_status_phase{{phase="Running",{K}}} == 1)', "pods running")], 72)
stat("k8s-now", [q(cpu("k8s"), "CPU", "A"), q(mem("k8s"), "memory", "B"), q(REQUESTS, "requests/min", "C")], 30, names=PCT)
chart("k8s-chart", "Requests through Traefik · 24 h", [series(REQUESTS, "requests")], "short")
# The probes run from a Pi but belong to what they reach, as on Home.
stat("k8s-facts", [q(f'sum(kube_node_status_condition{{condition="Ready",status="true",{K}}})', "nodes ready", "A"), q(f'count(flux_resource_info{{ready="True",{K}}})', "deploys ready", "B"),
                   q(f"count(probe_success{{{BB}}} == 1)", "services up", "C"), q(f"sum(longhorn_node_storage_usage_bytes{{{K}}})", "Longhorn used", "D")], 22, label=13,
     names=[by_name("Longhorn used", unit="decbytes")])

# ------------------------------------------------------------------ network
WAN_IN = f"8 * sum(rate(unpoller_device_wan_receive_bytes_total{{{UNI}}}[5m]))"
WAN_OUT = f"8 * sum(rate(unpoller_device_wan_transmit_bytes_total{{{UNI}}}[5m]))"
WAN_MS = f'1000 * label_replace({w.by_wan("unpoller_device_wan_monitor_latency_average_seconds")}, "wan", "$1", "wan", "([^ ]+) .*")'
heading("net-head", "Network", 76, "internet and Wi-Fi", 111)
state("net-state", "network")
stat("net-hero", [q(WAN_IN, "in", "A"), q(WAN_OUT, "out", "B")], 32, unit="bps", decimals=1)
stat("net-day", [q(f"sum(increase(unpoller_device_wan_receive_bytes_total{{{UNI}}}[24h]))", "in, 24 h", "A"),
                 q(f"sum(increase(unpoller_device_wan_transmit_bytes_total{{{UNI}}}[24h]))", "out, 24 h", "B")], 26, unit="decbytes")
# On a symmetric log scale, so the quiet hours keep a shape beside the busy ones. Download, the bigger of the two, is the darker.
chart("net-chart", "Internet · 24 h · in above the line, out below", [series(WAN_IN, "in", "A"), series(WAN_OUT, "out", "B")], "bps", fill=tint(DARK), width=1,
      overrides=[by_name("out", custom__transform="negative-Y", custom__fillOpacity=tint(LIGHT))], scaleDistribution={"type": "symlog", "log": 10, "linearThreshold": 1000000})
stat("net-facts", [q(WAN_MS, "{{wan}}", "A"), q(f'sum(unpoller_device_stations{{{UNI},station_type="user"}})', "clients", "B"),
                   q(f"count(unpoller_device_info{{{UNI}}})", "UniFi devices", "C")], 22, label=13, names=[by_ref("A", unit="ms")])

# ------------------------------------------------------------------ Sparks
heading("spark-head", "Sparks", 58, "inference", 58)
state("spark-state", "spark")
stat("spark-hero", [q("100 * max(nvidia_smi_utilization_gpu_ratio)", "GPU busy", "A"), q("sum(nvidia_smi_power_draw_watts)", "drawing", "B")], 36,
     names=[by_name("GPU busy", unit="percent"), by_name("drawing", unit="watt")])
# Stacked, spark-1 dark under spark-2 light, each edged in black, so either one's share reads at a glance.
chart("spark-chart", "GPU power, both Sparks · 24 h", [series("max by (instance) (nvidia_smi_power_draw_watts)", "{{instance}}")], "watt", fill=tint(LIGHT), width=1,
      overrides=[by_name("spark-1", custom__fillOpacity=tint(DARK))], stacking={"mode": "normal", "group": "A"})
# TensorFold reports only while it serves a model, so its numbers drop out rather than read zero while none is loaded.
stat("spark-facts", [q("sum(tensorfold:requests_running)", "requests", "A"), q("sum(increase(tensorfold:generation_tokens_total[24h]))", "tokens, 24 h", "B"),
                     q("max(nvidia_smi_temperature_gpu)", "hottest GPU", "C")], 22, label=13,
     names=[by_name("tokens, 24 h", unit="short"), by_name("hottest GPU", unit="celsius")])

# ------------------------------------------------------------------ Proxmox
heading("pve-head", "Proxmox", 76, "pve-sbx", 50)
state("pve-state", "pve-sbx")
stat("pve-facts", [q(f"count(({w.GUEST}) == 1)", "guests up", "A"), q('count(max by (id) (pve_up{id=~"node/.*"}) == 1)', "nodes up", "B"),
                   q(cpu("pve"), "CPU", "C"), q(mem("pve"), "memory", "D")], 26, label=13, names=PCT)
# Each guest's memory against what it was given, as on Home's guest table.
fullness("pve-guests", f'clamp_max(100 * {w.G("pve_memory_usage_bytes")} / {w.G("pve_memory_size_bytes")}, 100)', "name", 0.35)

# ------------------------------------------------------------------ Athena
VOL = f'{SYN},raidName=~"Volume.*"'
heading("nas-head", "Athena", 61, "storage", 47)
state("nas-state", "athena")
stat("nas-hero", [q(f"sum(raidFreeSize{{{VOL}}})", "free", "A"), q(f"sum(raidTotalSize{{{VOL}}})", "in all", "B")], 40, unit="decbytes", decimals=1)
fullness("nas-bar", f"100 * (1 - max by (raidName) (raidFreeSize{{{VOL}}}) / max by (raidName) (raidTotalSize{{{VOL}}}))", "raidName", 0.6)
stat("nas-facts", [q(f"count(diskStatus{{{SYN}}} == 1)", "disks", "A"), q('max(drive:temperature_celsius{platform="synology"})', "hottest disk", "B"),
                   q(f"max(temperature{{{SYN}}})", "inside", "C")], 22, label=13, names=[by_name("hottest disk", unit="celsius"), by_name("inside", unit="celsius")])

# ------------------------------------------------------------------ power
heading("ups-head", "Power", 55, "UPS battery", 74)
state("ups-state", "power")
ranked("ups-bars", f"{w.U_RUNTIME} / 60", "ups", "$1 min", 0.45)
# The compute rack carries the servers and the Sparks, so its load is the one that moves.
chart("ups-chart", "Compute rack load · 24 h", [series(match(w.U_LOAD, "ups", "compute-rack"), "compute-rack")], "percent", hi=100)

# ------------------------------------------------------------------ devices
DEVICE_HEAT = 'max by (instance) (node_hwmon_temp_celsius{platform="pi"}) or max by (instance) (pikvm_hw_temp_cpu{job="glkvm-kvmd"})'
heading("dev-head", "Devices", 64, "Pis and console", 98)
state("dev-state", "devices")
stat("dev-facts", [q('count(up{job="pi-node"} == 1)', "Pis up", "A"), q('max(up{job="glkvm-kvmd"})', "console", "B"),
                   q('min(time() - node_boot_time_seconds{platform="pi"})', "up for", "C")], 22, label=13,
     names=[by_name("console", mappings=[{"type": "value", "options": {"1": {"text": "up", "index": 0}, "0": {"text": "down", "index": 1}}}]), by_name("up for", unit="dtdurations")])
P.add("dev-heat", "", "stat", [q(f"sort_desc({DEVICE_HEAT})", "{{instance}}")],
      {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "horizontal", "wideLayout": True, "textMode": "value_and_name", "showPercentChange": False,
       "reduceOptions": LAST, "text": {"titleSize": 14, "valueSize": 16}}, {"color": fixed(INK), "unit": "celsius", "decimals": 0, "noValue": "–"})
bare("dev-heat")

# ------------------------------------------------------------------ the week: Home's alert strip, in Home's levels, darker as they get worse
# The aside's width is estimated from the width measured for the earlier, shorter wording, with room to spare.
heading("week-head", "Alerts", 57, "worst alert on each system · darker is worse", 280)
P.add("week", "", "state-timeline", [q(w.QS(e.ordered(f"max_over_time(({e.ALEVEL})[30m:5m])")), "{{system}}", instant=False, interval="30m")],
      {"alignValue": "left", "legend": {"showLegend": False}, "mergeValues": True, "rowHeight": 0.8, "showValue": "never", "tooltip": {"mode": "none"}},
      {"color": {"mode": "thresholds"}, "thresholds": th([{"value": None, "color": PAGE}, *SEVERITY]), "custom": {"fillOpacity": 100, "lineWidth": 0, "spanNulls": False}},
      qopts=WEEK)
bare("week")

# ------------------------------------------------------------------ the page: (element, x, y, width, height) on Grafana's 24-column grid
LAYOUT = [
    ("masthead", 0, 0, 24, 1),
    ("headline", 0, 1, 14, 3), ("systems", 14, 1, 10, 3),
    ("k8s-head", 0, 4, 6, 1), ("k8s-state", 6, 4, 2, 1), ("k8s-hero", 0, 5, 3, 3), ("k8s-now", 3, 5, 5, 3), ("k8s-chart", 0, 8, 8, 6), ("k8s-facts", 0, 14, 8, 2),
    ("net-head", 8, 4, 8, 1), ("net-state", 16, 4, 2, 1), ("net-hero", 8, 5, 5, 3), ("net-day", 13, 5, 5, 3), ("net-chart", 8, 8, 10, 6), ("net-facts", 8, 14, 10, 2),
    ("spark-head", 18, 4, 4, 1), ("spark-state", 22, 4, 2, 1), ("spark-hero", 18, 5, 6, 3), ("spark-chart", 18, 8, 6, 6), ("spark-facts", 18, 14, 6, 2),
    ("pve-head", 0, 16, 4, 1), ("pve-state", 4, 16, 2, 1), ("pve-facts", 0, 17, 6, 3), ("pve-guests", 0, 20, 6, 4),
    ("nas-head", 6, 16, 3, 1), ("nas-state", 9, 16, 2, 1), ("nas-hero", 6, 17, 5, 3), ("nas-bar", 6, 20, 5, 2), ("nas-facts", 6, 22, 5, 2),
    ("ups-head", 11, 16, 4, 1), ("ups-state", 15, 16, 2, 1), ("ups-bars", 11, 17, 6, 3), ("ups-chart", 11, 20, 6, 4),
    ("dev-head", 17, 16, 5, 1), ("dev-state", 22, 16, 2, 1), ("dev-facts", 17, 17, 7, 3), ("dev-heat", 17, 20, 7, 4),
    ("week-head", 0, 24, 24, 1), ("week", 0, 25, 24, 5)]
ROWS_ON_PAGE = 30   # 30 rows of 30 px with 8 px gaps fit 1200 px in kiosk mode; a 31st scrolls


def check(layout):
    """Every cell of the page is used exactly once, so Grafana has nothing to float upwards and nothing scrolls."""
    cells = {}
    for name, x, y, wd, h in layout:
        assert 0 <= x < x + wd <= 24 and 0 <= y < y + h <= ROWS_ON_PAGE, f"{name} extends beyond the page"
        for cell in ((cx, cy) for cx in range(x, x + wd) for cy in range(y, y + h)):
            assert cell not in cells, f"{name} overlaps {cells[cell]}"
            cells[cell] = name
    assert len(cells) == 24 * ROWS_ON_PAGE, f"{24 * ROWS_ON_PAGE - len(cells)} cells of the page are empty"


def build():
    check(LAYOUT)
    elements = {name: P.elements[name] for name, *_ in LAYOUT}
    assert set(elements) == set(P.elements), f"not placed: {set(P.elements) - set(elements)}"
    for i, v in enumerate(elements.values(), 1):
        v["spec"]["id"] = i
    for v in elements.values():
        for pq in v["spec"]["data"]["spec"]["queries"]:
            if pq["spec"]["query"]["group"] == "datasource":
                pq["spec"]["query"]["spec"]["panelId"] = elements["systems"]["spec"]["id"]
    items = [{"kind": "GridLayoutItem", "spec": {"x": x, "y": y, "width": wd, "height": h, "element": ref(name)}} for name, x, y, wd, h in LAYOUT]
    d = dashboard("home-eink", "Home · E-paper", "The whole lab on one page for a 1600×1200 four-grey e-paper display: Home's health judgement, then each system's numbers "
                  "and its last day. Capture the whole dashboard in kiosk mode with the light theme.", ["overview", "e-paper"], [datasource_variable()], elements,
                  {"kind": "GridLayout", "spec": {"items": items}})
    s = d["spec"]
    s.update({"cursorSync": "Off", "preload": True})
    s["timeSettings"].update({"autoRefresh": "1m", "autoRefreshIntervals": ["1m", "5m", "15m"], "hideTimepicker": True})
    return d
