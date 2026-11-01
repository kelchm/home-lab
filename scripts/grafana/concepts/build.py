#!/usr/bin/env python3
"""Build the Spark dashboard concepts from the #1214 dashboards. A concept is a look, not a layout: every panel takes a role, and each concept colours the roles.

Writes concepts/<name>/{inference,health}.json, ready to import. With --lab, also writes copies for a scratch Grafana replaying a fixed window of real data.
"""
import argparse
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "kubernetes/apps/observability/grafana/app"
HERE = Path(__file__).resolve().parent
LAB = Path("/tmp/spark-lab/dash")
WINDOW = ("2026-10-31T23:00:00.000Z", "2026-11-01T01:30:00.000Z")

INFO = "#6CA6E0"      # a plain quantity: information, with no good or bad in it
RULE = {"ok": "#3ECF8E", "drawn": True, "speed": INFO, "load": INFO, "cache": INFO, "memory": INFO, "heat": INFO, "context": INFO, "fine": "#5F7690",
        "muted": "#52525B", "warn": "#F5A524", "bad": "#FF6B6B", "info": "#71717A", "band": 14, "quiet": "#1C1F24", "good": "#17402F", "warm": "#4A3A12", "hot": "#4F2020",
        "heatmap": {"mode": "opacity", "fill": INFO, "scale": "exponential", "exponent": 0.5}, "line": 1.5, "fill": 8, "gradient": "opacity", "transparent": True}
GIB = 1 << 30

# Roles: spark1/spark2 are the machines. speed, load, cache, memory, heat and context are what a tile or single-series chart measures. fine is a second series beside
# speed. muted is context drawn behind data. warn, bad and info colour state bands. quiet, warm and hot fill the tiles with no history.
CONCEPTS = {
    "one-dark": {
        "ok": "#98C379", "title": "One Dark",
        "spark1": "#61AFEF", "spark2": "#C678DD",
        "speed": "#98C379", "load": "#61AFEF", "cache": "#C678DD", "memory": "#56B6C2", "heat": "#D19A66", "context": "#56B6C2", "fine": "#D19A66",
        "muted": "#7F848E", "warn": "#E5C07B", "bad": "#E06C75", "info": "#ABB2BF", "band": 18,
        "quiet": "#2C313A", "good": "#2F4A35", "warm": "#5E4A2A", "hot": "#5E2B30",
        "heatmap": {"mode": "opacity", "fill": "#61AFEF", "scale": "exponential", "exponent": 0.45},
        "line": 2, "fill": 14, "gradient": "opacity", "transparent": False,
    },
    "aurora": {
        "ok": "#34D399", "title": "Aurora",
        "spark1": "#2DD4BF", "spark2": "#A78BFA",
        "speed": "#34D399", "load": "#38BDF8", "cache": "#A78BFA", "memory": "#2DD4BF", "heat": "#F472B6", "context": "#2DD4BF", "fine": "#A78BFA",
        "muted": "#64748B", "warn": "#FBBF24", "bad": "#FB7185", "info": "#94A3B8", "band": 16,
        "quiet": "#1B2A33", "good": "#0F4A42", "warm": "#5A4312", "hot": "#5A1F33",
        "heatmap": {"mode": "scheme", "scheme": "Viridis", "scale": "exponential", "exponent": 0.5, "steps": 64},
        "line": 2, "fill": 24, "gradient": "hue", "transparent": False,
    },
    "dusk": {
        "ok": "#9ECE6A", "title": "Dusk",
        "spark1": "#FF9E64", "spark2": "#7DCFFF",
        "speed": "#FF9E64", "load": "#7DCFFF", "cache": "#7DCFFF", "memory": "#7DCFFF", "heat": "#FF9E64", "context": "#FF9E64", "fine": "#7DCFFF",
        "muted": "#737AA2", "warn": "#E0AF68", "bad": "#F7768E", "info": "#A9B1D6", "band": 16,
        "quiet": "#24283B", "good": "#2A3A52", "warm": "#594520", "hot": "#5C2A3A",
        "heatmap": {"mode": "scheme", "scheme": "Inferno", "scale": "exponential", "exponent": 0.5, "steps": 64},
        "line": 2, "fill": 16, "gradient": "opacity", "transparent": False,
    },
    "graphite": {
        "ok": "#C8F751", "title": "Graphite",
        "spark1": "#F2F3F5", "spark2": "#C8F751",
        "speed": "#C8F751", "load": "#C8F751", "cache": "#C8F751", "memory": "#C8F751", "heat": "#C8F751", "context": "#F2F3F5", "fine": "#F2F3F5",
        "muted": "#6B7280", "warn": "#FFB020", "bad": "#FF5C5C", "info": "#9CA3AF", "band": 14,
        "quiet": "#1C1F24", "good": "#2C3A16", "warm": "#4A3A12", "hot": "#4F2020",
        "heatmap": {"mode": "opacity", "fill": "#C8F751", "scale": "exponential", "exponent": 0.45},
        "line": 2, "fill": 8, "gradient": "opacity", "transparent": True,
    },
    # Round two: each concept is one rule for what colour means. Bars draw activity, thin lines draw levels, and nothing is filled behind two machines.
    # Quantities have no good or bad, so they take the information blue. A judged value has a limit, some from the alert rules and some still placeholders, and its line changes colour as it crosses one.
    "colour-judges": dict(RULE, title="Colour judges", calm=INFO, calm1="#9CC4F0", calm2="#5B86BC", spark1="#9CC4F0", spark2="#5B86BC"),
    "traffic-light": dict(RULE, title="Traffic light", calm="#3ECF8E", calm1="#3ECF8E", calm2="#A7F3D0", spark1="#9CC4F0", spark2="#5B86BC"),
    "machines": dict(RULE, title="Machines and condition", calm="#3ECF8E", calm1="#2DD4BF", calm2="#A78BFA", spark1="#2DD4BF", spark2="#A78BFA"),
}

INFERENCE = {4: "load", 3: "speed", 34: "speed", 26: "cache", 21: "memory", 35: "context"}
HEALTH = {1: "memory", 2: "heat"}
SINGLE = {33: "context", 22: "speed", 23: "fine", 10: "load", 27: "memory", 29: "speed", 14: "cache"}
BY_NAME = {7: {"Live": "muted", "One-minute trend": "speed"}, 8: {"First token": "speed", "Whole request": "load"}, 11: {"Running": "load", "Waiting": "warn", "Capacity": "muted"},
           13: {"New": "speed", "From cache": "cache", "All tokens": "muted"}}
OLD = {"#73A650": "spark1", "#5399E2": "spark2", "#7B8494": "muted", "#FF9830": "warn", "#E02F44": "bad", "#2E333B": "quiet", "#6B4A17": "warm", "#6E2323": "hot", "#26492F": "good", "orange": "warn", "red": "bad"}


def fixed(c):
    return {"fixedColor": c, "mode": "fixed"}


def largest_request(d):
    """Give the per-request bars' job to a tile, and the chart the whole row: nothing runs most of the time, so the bars were usually empty."""
    panels = d["panels"]
    bars = next(p for p in panels if p["id"] == 32)
    chart = next(p for p in panels if p["id"] == 33)
    selector = re.search(r"tensorfold:kv_cache_usage_ratio(\{[^}]*\})", bars["targets"][0]["expr"]).group(1)
    tile = copy.deepcopy(next(p for p in panels if p["id"] == 21))
    tile.update(id=35, title="Largest request", description="The largest running request's share of its context window. Idle when nothing is running.")
    tile["targets"] = [{**tile["targets"][0], "expr": f"100 * max(tensorfold:kv_cache_usage_ratio{selector}) or on() vector(0)"}]
    tile["fieldConfig"]["defaults"]["unit"] = "percent"
    tile["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {"0": {"text": "Idle", "index": 0}}}]
    panels.remove(bars)
    chart["gridPos"].update(x=0, w=24)
    panels.insert(next(i for i, p in enumerate(panels) if p["id"] == 26), tile)
    x = 0
    for id_, w in ((20, 3), (4, 3), (3, 4), (34, 4), (35, 4), (26, 3), (21, 3)):
        p = next(p for p in panels if p["id"] == id_)
        p["gridPos"].update(x=x, y=2, w=w, h=4)
        x += w


def recolour(value, c):
    if isinstance(value, dict):
        return {k: (c[OLD[v]] if isinstance(v, str) and k in ("color", "fixedColor") and v in OLD else recolour(v, c)) for k, v in value.items()}
    return [recolour(v, c) for v in value] if isinstance(value, list) else value


def by_name(name, colour):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "color", "value": fixed(colour)}]}


def style_tile(p, role, c):
    defaults = p["fieldConfig"]["defaults"]
    if defaults.get("color", {}).get("mode") == "fixed":
        defaults["color"] = fixed(c[role])
    else:      # the tile's level matters: its first step takes the role's colour
        steps = defaults["thresholds"]["steps"]
        steps[-1 if role == "memory" else 0]["color"] = c[role]


def card(p, c):
    """A KPI card: the name and value at the top left, its history below them. Grafana left-aligns a value only when it has a name, so the title moves into the tile.
    A tile with no history keeps a plain surface and colours its text, so a good state recedes and only a bad one draws the eye."""
    defaults, options = p["fieldConfig"]["defaults"], p["options"]
    many = len(p["targets"]) > 1
    per_node = "instance" in p["targets"][0].get("legendFormat", "")
    if not many:
        short = {"Memory available": "Memory", "Hottest part": "Hottest", "Fullest disk": "Disk"}.get(p["title"], p["title"])
        defaults["displayName"] = f"{short} · ${{__field.labels.instance}}" if per_node else p["title"]
    # A fixed scale keeps each history in the card's lower part, clear of the value above it.
    top = {"Requests": 6.5, "Live output": 160, "Reply speed": 130, "Largest request": 100, "Memory available": 17.2e9, "Hottest part": 165}.get(p["title"])
    if p["title"] in ("Cache hits", "Pool used"):
        top = 1.3 if defaults.get("unit") == "percentunit" else 130
    if top:
        defaults.update(min=30 if p["title"] == "Hottest part" else 0, max=top)
    p["title"] = ""
    options.update(textMode="value_and_name", justifyMode="auto", text={"titleSize": 13, "valueSize": 26})
    if options.get("colorMode") == "background_solid":
        options["colorMode"] = "value"
        calm = {c["quiet"]: "text", c["warm"]: c["warn"], c["hot"]: c["bad"], c["good"]: c["ok"]}
        for key in ("thresholds", "mappings"):
            if key in defaults:
                defaults[key] = json.loads(re.sub("|".join(calm), lambda m: calm[m.group(0)], json.dumps(defaults[key])))


def taller_tiles(d):
    """One more row of height keeps a tall history clear of the value above it."""
    tiles = [p for p in d["panels"] if p["type"] == "stat" and p["title"]]
    bottom = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in tiles)
    for p in d["panels"]:
        if p["gridPos"]["y"] >= bottom:
            p["gridPos"]["y"] += 1
    for p in tiles:
        p["gridPos"]["h"] += 1


# Limits, as (from, condition), lowest first. "pct" scales to the panel's unit.
HEAT = [(None, "calm"), (75, "warn"), (85, "bad")]
BOARD = [(None, "calm"), (95, "warn"), (99.8, "bad")]
LOW_MEMORY = [(None, "bad"), (2 * GIB, "warn"), (4 * GIB, "calm")]
SLOW = [(None, "bad"), (8, "warn"), (15, "calm")]
POOL = [(None, "calm"), ("90", "warn"), ("98", "bad")]
JUDGED = {"inference": {33: [(None, "calm"), (750000, "warn"), (900000, "bad")], 22: SLOW, 8: [(None, "calm"), (60, "warn"), (300, "bad")], 27: POOL},
          "health": {7: HEAT, 17: BOARD, 10: LOW_MEMORY, 12: HEAT}}
JUDGED_TILES = {"inference": {34: SLOW, 35: [(None, "calm"), (75, "warn"), (90, "bad")], 26: [(None, "bad"), ("50", "warn"), ("80", "calm")], 21: POOL}, "health": {1: LOW_MEMORY, 2: BOARD}}


def steps(spec, c, calm, unit):
    def at(v):
        return float(v) / (100 if unit == "percentunit" else 1) if isinstance(v, str) else v
    return {"mode": "absolute", "steps": [{"value": at(v), "color": calm if r == "calm" else c[r]} for v, r in spec]}


def by_series(fc, c, spec, unit):
    """Each machine keeps its own calm colour, so its line stays identifiable until it crosses a limit."""
    for o in fc.get("overrides", []):
        name = str(o["matcher"].get("options"))
        calm = c["calm1"] if "spark-1" in name else c["calm2"] if "spark-2" in name else None
        if calm:
            o["properties"] = [x for x in o["properties"] if x["id"] != "color"] + [{"id": "color", "value": {"mode": "thresholds"}}, {"id": "thresholds", "value": steps(spec, c, calm, unit)}]


def judge(p, c, spec):
    fc = p["fieldConfig"]
    defaults = fc["defaults"]
    unit = defaults.get("unit")
    defaults["thresholds"] = steps(spec, c, c["calm"], unit)
    defaults["color"] = {"mode": "thresholds"}
    if p["type"] == "timeseries":
        defaults["custom"].update(gradientMode="scheme", thresholdsStyle={"mode": "off"}, fillOpacity=0)   # a fill would paint the limits' colours under a healthy line
        for o in fc.get("overrides", []):
            if any(x["id"] == "custom.lineWidth" and x["value"] == 0 for x in o["properties"]):
                o["properties"].append({"id": "custom.gradientMode", "value": "none"})
        if p["id"] == 8:
            fc["overrides"] = [o for o in fc["overrides"] if o["matcher"].get("options") not in ("First token", "Whole request")]
            fc["overrides"].append({"matcher": {"id": "byName", "options": "Whole request"}, "properties": [{"id": "thresholds", "value": steps(spec, c, c["fine"], unit)}]})
    else:
        p["options"]["colorMode"] = "value"
        fc.setdefault("overrides", [])
        for machine, calm in (("spark-1", c["calm1"]), ("spark-2", c["calm2"])):
            if "instance" in p["targets"][0].get("legendFormat", ""):
                fc["overrides"].append({"matcher": {"id": "byRegexp", "options": f"/{machine}/"}, "properties": [{"id": "thresholds", "value": steps(spec, c, calm, unit)}]})
    by_series(fc, c, spec, unit)


TWO_MACHINES = (7, 8, 15, 10, 17, 12, 30, 9)
BARS = (23, 10)


def drawn(p, c, health):
    """Round two: activity as bars, levels as thin smooth lines, and nothing filled behind two lines that would only blend."""
    fc = p["fieldConfig"]
    custom = fc["defaults"]["custom"]
    custom.update(lineInterpolation="smooth", axisBorderShow=False)
    fc["overrides"].append({"matcher": {"id": "byType", "options": "time"}, "properties": [{"id": "custom.axisGridShow", "value": False}]})
    if health and p["id"] in TWO_MACHINES:
        custom["fillOpacity"] = 0
    if not health and p["id"] in BARS:
        custom.update(drawStyle="bars", barAlignment=0, barWidthFactor=0.8, lineWidth=0, fillOpacity=30, gradientMode="none")
        fc["defaults"]["color"] = fixed(c["speed"] if p["id"] == 23 else c["load"])
    if not health and p["id"] == 7:
        for o in fc["overrides"]:
            if o["matcher"].get("options") == "Live":
                o["properties"] = [x for x in o["properties"] if x["id"] != "custom.lineWidth" and x["id"] != "custom.fillOpacity"]
                o["properties"] += [{"id": "custom.drawStyle", "value": "bars"}, {"id": "custom.fillOpacity", "value": 55}, {"id": "custom.lineWidth", "value": 0}, {"id": "custom.barWidthFactor", "value": 0.8}, {"id": "custom.gradientMode", "value": "none"}]
    if not health and p["id"] == 11:
        fc["overrides"].append({"matcher": {"id": "byName", "options": "Running"}, "properties": [{"id": "custom.drawStyle", "value": "bars"}, {"id": "custom.fillOpacity", "value": 45}, {"id": "custom.lineWidth", "value": 0}, {"id": "custom.barWidthFactor", "value": 0.8}, {"id": "custom.gradientMode", "value": "none"}]})


def style(d, c, roles):
    if not c.get("drawn"):
        taller_tiles(d)
    health = any(p.get("title") == "GPU temperature" for p in d["panels"])
    for p in d["panels"]:
        if p["type"] in ("row", "text"):
            continue
        if c["transparent"] and p["type"] != "stat":
            p["transparent"] = True
        if p["type"] == "stat" and p["id"] in roles:
            style_tile(p, roles[p["id"]], c)
        if p["type"] == "stat" and p["title"]:
            card(p, c)
            p["maxDataPoints"] = 120 if c.get("drawn") else p.get("maxDataPoints")
        if p["type"] == "heatmap":
            p["options"]["color"] = {"reverse": False, "steps": 64, **c["heatmap"]}
            p["options"].setdefault("cellGap", 1)
            p["options"]["cellGap"] = 1
        if p["type"] != "timeseries":
            continue
        fc = p["fieldConfig"]
        custom = fc["defaults"]["custom"]
        if custom.get("drawStyle") != "points":
            custom.update(lineWidth=c["line"], gradientMode=c["gradient"], showPoints="never")
            custom["fillOpacity"] = max(custom.get("fillOpacity") or 0, c["fill"])
            if custom.get("drawStyle") == "bars":
                custom.update(fillOpacity=70, gradientMode="hue", lineWidth=0)
        for o in fc.get("overrides", []):
            is_band = any(x["id"] == "custom.lineWidth" and x["value"] == 0 for x in o["properties"])
            for x in o["properties"]:
                if is_band and x["id"] == "custom.fillOpacity":
                    x["value"] = c["band"]
            if o["matcher"]["id"] == "byRegexp" and "max" in o["matcher"]["options"] or "ConnectX" in str(o["matcher"].get("options")) or "Capacity" in str(o["matcher"].get("options")):
                o["properties"].append({"id": "custom.fillOpacity", "value": 0})
        if p["id"] in SINGLE:
            fc["defaults"]["color"] = fixed(c[SINGLE[p["id"]]])
        fc["overrides"] = [o for o in fc.get("overrides", []) if not (o["matcher"]["id"] == "byName" and o["matcher"]["options"] in BY_NAME.get(p["id"], {}) and {x["id"] for x in o["properties"]} == {"color"})]
        for name, role in BY_NAME.get(p["id"], {}).items():
            fc["overrides"].append(by_name(name, c[role]))
        for o in fc["overrides"]:
            for x in o["properties"]:
                if x["id"] == "custom.lineWidth" and x["value"] == 2:
                    x["value"] = c["line"]
        if p["id"] == 7:
            for o in fc["overrides"]:
                if o["matcher"].get("options") == "Live":
                    o["properties"] += [{"id": "custom.lineWidth", "value": 1}, {"id": "custom.fillOpacity", "value": 0}]
        if c.get("drawn"):
            drawn(p, c, health)


def concept(file, roles, c, uid, title):
    d = recolour(json.loads((APP / file).read_text()), c)
    if file.startswith("spark-tensorfold"):
        largest_request(d)
    style(d, c, roles)
    if "calm" in c:
        kind = "inference" if file.startswith("spark-tensorfold") else "health"
        for p in d["panels"]:
            spec = JUDGED_TILES[kind].get(p["id"]) if p["type"] == "stat" else JUDGED[kind].get(p["id"]) if p["type"] == "timeseries" else None
            if spec:
                judge(p, c, spec)
        if kind == "health":   # a power-cap flag is ordinary operation here, so it is a fact rather than a warning
            for o in next(p for p in d["panels"] if p["id"] == 8)["fieldConfig"]["overrides"]:
                for x in o["properties"]:
                    if x["id"] == "color" and x["value"].get("fixedColor") == c["warn"]:
                        x["value"] = fixed(c["info"])
    d.update(uid=uid, title=f"Concept · {c['title']} · {title}", id=None, version=0, editable=True, tags=["concept"], links=[])
    return d


def for_lab(d):
    """The window's samples are 30 seconds apart, so nothing can ask for finer steps."""
    d = copy.deepcopy(d)
    d["time"] = {"from": WINDOW[0], "to": WINDOW[1]}
    d["refresh"] = ""
    for p in d["panels"]:
        if p.get("interval") in ("1s", "5s", "15s"):
            p["interval"] = "30s"
        for t in p.get("targets", []):
            if t.get("interval") in ("1s", "5s", "15s"):
                t["interval"] = "30s"
    return d


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--lab", action="store_true")
parser.add_argument("--only")
args = parser.parse_args()
for key, c in CONCEPTS.items():
    if args.only and key != args.only:
        continue
    out = HERE / key
    out.mkdir(exist_ok=True)
    for name, file, roles, title in (("inference", "spark-tensorfold-dashboard.json", INFERENCE, "Inference"), ("health", "sparks-dashboard.json", HEALTH, "Sparks")):
        d = concept(file, roles, c, f"concept-{key}-{name}", title)
        (out / f"{name}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
        if args.lab:
            (LAB / f"{key}-{name}.json").write_text(json.dumps(for_lab(d), ensure_ascii=False))
