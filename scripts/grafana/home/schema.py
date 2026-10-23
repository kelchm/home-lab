"""Building blocks for dashboards in Grafana 13's v2 resource format (dashboard.grafana.app/v2).

A dashboard holds named panel elements and a layout that places them by name.
"""

DS = {"name": "${datasource}"}
LOGS = {"name": "victorialogs"}
NOALERT = 'alertname!~"Watchdog|InfoInhibitor"'
FIRE = f'((time() - ALERTS_FOR_STATE{{{NOALERT}}}) and ignoring (alertstate) ALERTS{{alertstate="firing"}})'

# Colour rule: grey means fine; yellow means worth a look; orange and red mean warning and critical; blue is left to links.
# How busy a thing is only makes its grey brighter.
NEUTRAL = "#2E333B"
LOOK = "#E0B400"
FINE = "#7B8494"   # the fill of a bar that is nowhere near its limit
OTHER = "#9DA5B4"
# Line colours by entity, checked for colour-blind separation on the dark panel; none is a status colour.
SERIES = ["#73A650", "#5399E2", "#A24F88"]
LOAD = [{"value": None, "color": "#5C6370"}, {"value": 30, "color": "#7B8494"}, {"value": 60, "color": "#9DA5B4"}, {"value": 85, "color": "#C7CDD8"}]
QUIET = [{"value": None, "color": NEUTRAL}]


def th(steps):
    return {"mode": "absolute", "steps": steps}


PROBLEM = th(QUIET + [{"value": 1, "color": "orange"}])
BROKEN = th(QUIET + [{"value": 1, "color": "red"}])
NOTICE = th(QUIET + [{"value": 1, "color": LOOK}])
RUNS_OUT = th([{"value": None, "color": FINE}, {"value": 80, "color": "orange"}, {"value": 90, "color": "red"}])
HEADROOM = th([{"value": None, "color": "red"}, {"value": 5, "color": "orange"}, {"value": 15, "color": "text"}])
ROOM_TILE = th([{"value": None, "color": "red"}, {"value": 5, "color": "orange"}, {"value": 15, "color": NEUTRAL}])
TILE_LOAD = th(QUIET + [{"value": 30, "color": "#3D434E"}, {"value": 60, "color": "#4E5563"}, {"value": 85, "color": "#626B7B"}])
NODASH = [{"type": "special", "options": {"match": "null", "result": {"text": "–", "color": "text", "index": 0}}}]
GAUGE = {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}
TEXT = {"type": "color-text"}


# ------------------------------------------------------------------ queries
def ls(expr, **labels):
    return f"label_set({expr}, " + ", ".join(f'"{k}", "{v}"' for k, v in labels.items()) + ")"


def union(*xs):
    return " or ".join(f"({x})" for x in xs)


def match(expr, label, value):
    return f'label_match({expr}, "{label}", "^{value}$")'


def zero(expr):
    return f"({expr}) or vector(0)"


def q(expr, legend="", ref="A", instant=True, fmt=None, interval=None):
    spec = {"editorMode": "code", "expr": expr, "legendFormat": legend, "instant": instant, "range": not instant}
    if fmt:
        spec["format"] = fmt
    if interval:
        spec["interval"] = interval
    return {"kind": "PanelQuery", "spec": {"query": {"kind": "DataQuery", "group": "prometheus", "version": "v0", "datasource": DS, "spec": spec}, "refId": ref, "hidden": False}}


def lq(expr, kind, ref="A", **extra):
    """A VictoriaLogs query: kind is instant for log lines, or stats, statsRange or hits."""
    spec = {"editorMode": "code", "expr": expr, "queryType": kind, **extra}
    return {"kind": "PanelQuery", "spec": {"query": {"kind": "DataQuery", "group": "victoriametrics-logs-datasource", "version": "v0", "datasource": LOGS, "spec": spec}, "refId": ref, "hidden": False}}


# ------------------------------------------------------------------ field config
def props(**p):
    return [{"id": k.replace("__", "."), "value": v} for k, v in p.items()]


def by_ref(ref, **p):
    return {"matcher": {"id": "byFrameRefID", "options": ref}, "properties": props(**p)}


def by_name(name, **p):
    return {"matcher": {"id": "byName", "options": name}, "properties": props(**p)}


def by_re(rx, **p):
    return {"matcher": {"id": "byRegexp", "options": rx}, "properties": props(**p)}


def fixed(c):
    return {"mode": "fixed", "fixedColor": c}


def words(**m):
    """Value mappings from keyword arguments: _1=("Healthy", None) maps 1 to the text, coloured when a colour is given."""
    def one(i, v):
        text, colour = v if isinstance(v, tuple) else (v, None)
        return {"text": text, "index": i} | ({"color": colour} if colour else {})
    return [{"type": "value", "options": {k.lstrip("_"): one(i, v) for i, (k, v) in enumerate(m.items())}}]


def yes(good, bad, colour="red"):
    return [{"type": "value", "options": {"1": {"text": good, "index": 0}, "0": {"text": bad, "color": colour, "index": 1}}}]


def add_links(element, field, links):
    """Data links on one column of a table."""
    overrides = element["spec"]["vizConfig"]["spec"]["fieldConfig"]["overrides"]
    for o in overrides:
        if o["matcher"]["options"] == field:
            o["properties"].append({"id": "links", "value": links})
            return element
    overrides.append(by_name(field, links=links))
    return element


def T(group, **options):
    """A transformation, named by its id."""
    return {"kind": "Transformation", "group": group, "spec": {"options": options}}


RED = {"calcs": ["lastNotNull"], "fields": "", "values": False}
TS = {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom", "calcs": []}, "tooltip": {"mode": "multi", "sort": "desc"}}
BG = {"reduceOptions": RED, "orientation": "horizontal", "showUnfilled": True, "valueMode": "color", "namePlacement": "left", "sizing": "auto",
      "minVizHeight": 14, "minVizWidth": 8, "maxVizHeight": 300, "legend": {"showLegend": False}}


TILE = {"colorMode": "background_solid", "graphMode": "none", "justifyMode": "center", "orientation": "auto", "reduceOptions": RED, "textMode": "value_and_name",
        "wideLayout": False, "text": {"titleSize": 13, "valueSize": 22}}


def ts_custom(**kw):
    c = {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2, "fillOpacity": 18, "gradientMode": "opacity", "showPoints": "never", "spanNulls": False,
         "stacking": {"mode": "none", "group": "A"}, "axisBorderShow": False, "axisPlacement": "auto", "thresholdsStyle": {"mode": "off"}}
    c.update(kw)
    return c


# ------------------------------------------------------------------ panels
class Panels:
    """Named panel elements, with one method for each kind of widget."""

    def __init__(self):
        self.elements = {}
        self.groups = {}   # tile group key -> (title, its tiles' element keys, the variable a single tile repeats by, whether its words need wide tiles)

    def add(self, key, title, group, queries, options, defaults, overrides=None, desc="", transformations=None, qopts=None, links=None):
        self.elements[key] = {"kind": "Panel", "spec": {"id": 0, "title": title, "description": desc, "links": links or [],
            "data": {"kind": "QueryGroup", "spec": {"queries": queries, "transformations": transformations or [], "queryOptions": qopts or {}}},
            "vizConfig": {"kind": "VizConfig", "group": group, "version": "", "spec": {"options": options, "fieldConfig": {"defaults": defaults, "overrides": overrides or []}}}}}
        return key

    def tiles(self, key, title, items, links=None, wide=False, **kw):
        """State tiles under one title, each item (name, expr, field properties) its own untitled panel, so a narrow screen can wrap them onto more lines.
        wide is for a group whose names or values are long words, which need two tiles to a line on a phone rather than three."""
        members = []
        for n, (name, expr, p) in enumerate(items, 1):
            defaults = {"color": {"mode": "thresholds"}, "thresholds": th(QUIET), "decimals": 0, "links": links or []}
            members.append(self.add(f"{key}-{n}", "", "stat", [q(expr, name)], {**TILE}, defaults, [by_ref("A", **p)] if p else [], **kw))
        self.groups[key] = (title, members, None, wide)
        return key

    def table(self, key, title, keylabel, keyname, cols, info=None, sort=None, desc_sort=True, keywidth=140, **kw):
        """Member table: one row per member. cols are (name, expr aggregated by the key label, field properties).
        info is (expr, {label: column name}, {label: field properties}) for text columns read from the labels of one query."""
        refs, n = iter("ABCDEFGHIJKL"), 1
        qs, rename, index = [], {keylabel: keyname}, {keylabel: 0}
        exclude, ov = {"Time": True, **{f"Time {i}": True for i in range(1, 13)}}, [by_name(keyname, custom__width=keywidth)]
        if info:
            r = next(refs)
            expr, labels = info[:2]
            qs.append(q(expr, ref=r, fmt="table"))
            exclude[f"Value #{r}"] = True
            for lab, name in labels.items():
                rename[lab], index[lab] = name, n
                n += 1
                if len(info) > 2 and lab in info[2]:
                    ov.append(by_name(name, **info[2][lab]))
        for name, expr, p in cols:
            r = next(refs)
            qs.append(q(expr, ref=r, fmt="table"))
            rename[f"Value #{r}"], index[f"Value #{r}"] = name, n
            n += 1
            if p:
                ov.append(by_name(name, **p))
        opts = {"cellHeight": "sm", "showHeader": True}
        if sort:
            opts["sortBy"] = [{"displayName": sort, "desc": desc_sort}]
        defaults = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "minWidth": 64}, "noValue": "–", "decimals": 0,
                    "color": {"mode": "thresholds"}, "thresholds": th([{"value": None, "color": "text"}])}
        transformations = [T("joinByField", byField=keylabel, mode="outerTabular"), T("organize", excludeByName=exclude, indexByName=index, renameByName=rename)]
        return self.add(key, title, "table", qs, opts, defaults, ov, transformations=transformations, **kw)

    def bars(self, key, title, expr, legend, thresholds=RUNS_OUT, unit="percent", **kw):
        """Against its own limit: one bar per thing, from empty to that thing's limit."""
        opts = dict(BG, displayMode="basic", valueMode="text", sizing="manual", minVizHeight=12, maxVizHeight=18, text={"titleSize": 13, "valueSize": 14})
        return self.add(key, title, "bargauge", [q(expr, legend)], opts, {"unit": unit, "decimals": 0, "min": 0, "max": 100, "color": {"mode": "thresholds"}, "thresholds": thresholds}, **kw)

    def trend(self, key, title, series, unit, lo=0, hi=None, names=None, **kw):
        d = {"unit": unit, "min": lo, "color": fixed(OTHER), "custom": ts_custom()}
        if hi is not None:
            d["max"] = hi
        named = names or [leg for leg, _ in series if "{{" not in leg]
        ov = [by_name(leg, color=fixed(c)) for leg, c in zip(named, SERIES)] or [by_re(f"/-{i}$/", color=fixed(c)) for i, c in enumerate(SERIES, 1)]
        return self.add(key, title, "timeseries", [q(e, leg, r, instant=False) for (leg, e), r in zip(series, "ABCD")], TS, d, ov, **kw)

    def note(self, key, content):
        self.add(key, "", "text", [], {"mode": "markdown", "content": content}, {})
        self.elements[key]["spec"]["transparent"] = True
        return key


# ------------------------------------------------------------------ layout
def ref(name):
    return {"kind": "ElementReference", "name": name}


def item(name, repeat=None, when=None):
    """An auto-grid item; when=True shows it only while its query returns rows, when=False only while it returns none."""
    s = {"element": ref(name)}
    if repeat:
        s["repeat"] = {"mode": "variable", "value": repeat}
    if when is not None:
        s["conditionalRendering"] = {"kind": "ConditionalRenderingGroup", "spec": {"visibility": "show", "condition": "and",
                                     "items": [{"kind": "ConditionalRenderingData", "spec": {"value": when}}]}}
    return {"kind": "AutoGridLayoutItem", "spec": s}


def autogrid(items, cols, px, width="standard"):
    """Panels of one size that wrap onto more lines when a line has no room; width is a named minimum column width or one in pixels."""
    columns = {"columnWidthMode": "custom", "columnWidth": width} if isinstance(width, int) else {"columnWidthMode": width}
    return {"kind": "AutoGridLayout", "spec": {"maxColumnCount": cols, **columns, "rowHeightMode": "custom", "rowHeight": px, "fillScreen": False, "items": items}}


def grid(h, *cells):
    """Panels side by side with unequal widths, out of 24. Heights are grid units of 30 px plus 8 px gaps."""
    items, x = [], 0
    for name, width in cells:
        items.append({"kind": "GridLayoutItem", "spec": {"x": x, "y": 0, "width": width, "height": h, "element": ref(name)}})
        x += width
    return {"kind": "GridLayout", "spec": {"items": items}}


def row(layout, title=None):
    return {"kind": "RowsLayoutRow", "spec": {"title": title or "", "collapse": False, "hideHeader": title is None, "layout": layout}}


def rows(*rs):
    return {"kind": "RowsLayout", "spec": {"rows": list(rs)}}


def tab(title, layout):
    return {"kind": "TabsLayoutTab", "spec": {"title": title, "layout": layout}}


def only_while(r, variable, pattern):
    """Show a row only while a variable's value matches a pattern."""
    r["spec"]["conditionalRendering"] = {"kind": "ConditionalRenderingGroup", "spec": {"visibility": "show", "condition": "and", "items": [
        {"kind": "ConditionalRenderingVariable", "spec": {"variable": variable, "operator": "matches", "value": pattern}}]}}
    return r


# ------------------------------------------------------------------ variables and the dashboard
def datasource_variable():
    return {"kind": "DatasourceVariable", "spec": {"name": "datasource", "pluginId": "prometheus", "refresh": "onDashboardLoad", "regex": "", "current": {"text": "", "value": ""},
                                                   "options": [], "multi": False, "includeAll": False, "hide": "hideVariable", "skipUrlSync": False, "allowCustomValue": True}}


ALL = {"text": ["All"], "value": ["$__all"]}


def qvar(name, query, regex, hide="dontHide", multi=False, all_=False, current=None):
    spec = {"name": name, "current": current or {"text": "", "value": ""}, "hide": hide, "refresh": "onDashboardLoad", "skipUrlSync": False,
            "query": {"kind": "DataQuery", "group": "prometheus", "version": "v0", "datasource": DS, "spec": {"query": query, "refId": name}},
            "regex": regex, "sort": "disabled", "definition": "", "options": [], "multi": multi, "includeAll": all_, "allowCustomValue": False}
    return {"kind": "QueryVariable", "spec": spec}


def dashboard(uid, title, desc, tags, variables, elements, layout):
    return {"apiVersion": "dashboard.grafana.app/v2", "kind": "Dashboard", "metadata": {"name": uid},
            "spec": {"title": title, "description": desc, "annotations": [], "cursorSync": "Crosshair", "editable": False, "links": [], "liveNow": False, "preload": False, "tags": tags,
                     "timeSettings": {"timezone": "browser", "from": "now-6h", "to": "now", "autoRefresh": "1m", "autoRefreshIntervals": ["30s", "1m", "5m", "15m"],
                                      "hideTimepicker": False, "fiscalYearStartMonth": 0},
                     "variables": variables, "elements": elements, "layout": layout}}


def exprs(dash):
    """Every metrics query in a dashboard, with where it is."""
    for key, el in dash["spec"]["elements"].items():
        for pq in el["spec"]["data"]["spec"]["queries"]:
            if pq["spec"]["query"]["group"] == "prometheus":
                yield key, pq["spec"]["query"]["spec"]["expr"]
    for v in dash["spec"]["variables"]:
        if v["kind"] == "QueryVariable":
            yield "var:" + v["spec"]["name"], v["spec"]["query"]["spec"]["query"]
