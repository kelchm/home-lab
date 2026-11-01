#!/usr/bin/env python3
"""Render the Home dashboard: every system at a glance, then a tab for each system.

Tabs, the auto grid and panels shown only while they have data need Grafana's v2 dashboard format, which a provisioned
file gets only as a full dashboard.grafana.app/v2 resource. The parts are in home/: schema helpers, the widgets each
class of thing gets, and the overview's rules and panels.
"""
import argparse
import copy
import json
from pathlib import Path

from home import overview as e
from home import widgets as w
from home.schema import Panels, autogrid, dashboard, datasource_variable, exprs, grid, item, only_while, row, rows, tab, tile_row

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "kubernetes/apps/observability/grafana/app/home-dashboard.json"
# A ConfigMap holds at most 1 MiB, and VictoriaMetrics rejects a query over 16,384 characters.
MAX_BYTES = 1_000_000
MAX_QUERY = 16_000


def d(uid):
    return f"/d/{uid}?${{__url_time_range}}"


DRIVES = d("f8f249a0-be78-41b1-97fe-8d0a92a71b93")
DEEPER = {
    "k8s-prod": [("Kubernetes / Views / Global", d("k8s_views_global")), ("Longhorn / Operations", d("longhorn-operations")), ("Drive health", DRIVES), ("cert-manager", d("cdhrcds8aosg0c")),
                 ("Traefik", d("n5bu_kv4k")), ("CloudNativePG", d("cloudnative-pg")), ("Identity", d("pan9z68")), ("Probes", d("pi-blackbox")), ("Cluster logs", w.explore("k8s.cluster.name:=k8s-prod"))],
    "pve-sbx": [("Proxmox via Prometheus", d("Dp7Cd57Zza")), ("Hosts", d("hosts")), ("Drive health", DRIVES), ("PVE logs", w.explore("service.namespace:=pve"))],
    "spark": [("Sparks / GPU and host health", d("sparks-health")), ("AI / Inference serving", d("spark-tensorfold-performance")), ("Hosts", d("hosts")), ("Drive health", DRIVES),
              ("Spark logs", w.explore("service.namespace:=spark"))],
    "athena": [("Synology / Storage and hardware health", d("synology-health")), ("Drive health", DRIVES), ("Hosts", d("hosts")), ("Athena logs", w.explore("service.namespace:=synology"))],
    "network": [("UniFi sites", d("9WaGWZaZk")), ("Switches", d("FsfxpWaZz")), ("Access points", d("g5wFWqxZk")), ("Gateway", d("4Yo8IZ-Wk")),
                ("Network logs", w.explore("service.namespace:=unifi"))],
    "power": [("Synology / Storage and hardware health", d("synology-health"))],
    "devices": [("Hosts", d("hosts")), ("NTP", d("ntp")), ("GLKVM / Recovery console", d("glkvm-console")), ("Probes", d("pi-blackbox")), ("Pi logs", w.explore("service.namespace:=pi"))]}

SOURCE = {}   # element name -> (widget, the platform or system it is built for)


def cells(h, *cs):
    """A row of panels with unequal widths, out of 24. "widget@arg" names a widget built for one platform or system."""
    named = []
    for key, width in cs:
        src, _, arg = key.partition("@")
        name = f"{src}-{arg}" if arg else src
        SOURCE[name] = (src, arg)
        named.append((name, width))
    return row(grid(h, *named))


def auto(cols, px, key, repeat=None, when=None):
    src, _, arg = key.partition("@")
    name = f"{src}-{arg}" if arg else src
    SOURCE[name] = (src, arg)
    return row(autogrid([item(name, repeat, when)], cols=cols, px=px))


def tiles(key, repeat=None, panels=w.W):
    for m in panels.groups[key][1]:
        SOURCE[m] = (m, "")
    return tile_row(panels.groups[key], repeat)


GO = cells(2, ("deeper", 24))
# Heights are in grid units of 30 px plus 8 px gaps; a table needs about 36 px a row plus 50 for its title and header.
PAGES = {
    "k8s-prod": [tiles("k8s-state"), cells(6, ("machines@k8s", 15), ("machine-cpu@k8s", 9)), cells(10, ("k8s-capacity", 7), ("k8s-deploys", 7), ("k8s-namespaces", 10)),
                 cells(9, ("feed@k8s-prod", 24)), tiles("volumes"), cells(5, ("databases", 24)), cells(6, ("ssd-drives@k8s", 24)), cells(12, ("routes", 12), ("certs", 12)),
                 cells(11, ("probes", 24)), GO],
    "pve-sbx": [tiles("pve-state"), cells(6, ("machines@pve", 15), ("machine-cpu@pve", 9)), cells(8, ("pve-guests", 15), ("pve-pools", 9)), cells(9, ("feed@pve-sbx", 24)),
                cells(6, ("ssd-drives@pve", 24)), GO],
    "spark": [tiles("gpu", repeat="gpu"), auto(1, 70, "inference-idle", when=False), only_while(tiles("inference"), "model", ".+"), only_while(cells(7, ("output", 12), ("prompt-new", 12)), "model", ".+"),
              cells(5, ("machines@spark", 15), ("machine-cpu@spark", 9)), cells(7, ("gpu-use", 24)), auto(1, 334, "feed@spark", when=True), cells(5, ("ssd-drives@spark", 24)), GO],
    "athena": [tiles("nas-state"), cells(4, ("machines@synology", 15), ("nas-volumes", 9)), cells(9, ("feed@athena", 24)), cells(10, ("hdd-drives", 24)), GO],
    "network": [cells(12, ("netdev", 24)), cells(9, ("feed@network", 24)), cells(5, ("wan", 24)), tiles("starlink"), tiles("tmobile"), cells(11, ("wifi", 24)), GO],
    "power": [tiles("ups", repeat="ups"), cells(7, ("ups-load", 12), ("ups-runtime", 12)), GO],
    "devices": [cells(6, ("machines@pi", 15), ("machine-cpu@pi", 9)), cells(9, ("feed@devices", 24)), tiles("glkvm"), GO]}
# The repeat variables a system's widgets may use; anything else would be a widget on the wrong tab.
VARIABLES = {"spark": ("$gpu", "$model"), "power": ("$ups", "${ups")}


def names(layout):
    """Every element a layout refers to."""
    if isinstance(layout, dict):
        if layout.get("kind") == "ElementReference":
            yield layout["name"]
        for v in layout.values():
            yield from names(v)
    elif isinstance(layout, list):
        for v in layout:
            yield from names(v)


def prefixed(layout, prefix):
    """A copy of a layout whose element references carry a prefix, so every tab keeps its own panels."""
    if isinstance(layout, dict):
        if layout.get("kind") == "ElementReference":
            return {**layout, "name": prefix + layout["name"]}
        return {k: prefixed(v, prefix) for k, v in layout.items()}
    return [prefixed(v, prefix) for v in layout] if isinstance(layout, list) else layout


def deeper(c):
    P = Panels()
    P.note("deeper", "**Go deeper** &nbsp; " + " &nbsp;·&nbsp; ".join(f"[{title}]({url})" for title, url in DEEPER[c]))
    return P.elements["deeper"]


def stamp(name):
    src, arg = SOURCE[name]
    return w.BUILDERS[src](arg) if arg else copy.deepcopy(w.W.elements[src])


def everything():
    """The first tab: one row per system, then what needs attention, the services from outside, the watch itself and the inventory."""
    els = {**copy.deepcopy(e.E.elements), **e.needs_attention()}
    # A tab has no icon in the schema, so a symbol leads each system's name. Links read the tab's address from the hidden tab column.
    named = [{"type": "value", "options": {c: {"text": f"{w.GLYPH[c]} {c}", "index": i} for i, c in enumerate(w.SYSTEMS)}}]
    link = {"title": "Open ${__value.raw}", "url": "/d/home?dtab=${__data.fields.tab}&${__url_time_range}"}
    for key in ("board", "attention"):
        for o in els[key]["spec"]["vizConfig"]["spec"]["fieldConfig"]["overrides"]:
            if o["matcher"]["options"] == "System":
                o["properties"] += [{"id": "links", "value": [link]}, {"id": "mappings", "value": named}]
    inventory = els["inventory"]["spec"]
    for t in inventory["data"]["spec"]["transformations"]:
        if t["group"] == "organize":
            t["spec"]["options"]["renameByName"].update({c.replace("-", "_"): f"{w.GLYPH[c]} {c}" for c in w.SYSTEMS})
    for o in inventory["vizConfig"]["spec"]["fieldConfig"]["overrides"]:
        if o["matcher"]["options"] in w.SYSTEMS:
            o["matcher"]["options"] = f'{w.GLYPH[o["matcher"]["options"]]} {o["matcher"]["options"]}'
    layout = rows(row(autogrid([item("board"), item("strip")], cols=2, px=314)),
                  row(autogrid([item("attention", when=True), item("quiet", when=False)], cols=1, px=300)),
                  tiles("reach", panels=e.E), tiles("watch", panels=e.E), row(autogrid([item("inventory")], cols=1, px=760)))
    return els, tab(f'{w.GLYPH["Everything"]} Everything', layout)


def system(c):
    """One system's tab: what needs attention on it, then its own widgets."""
    els = e.needs_attention(c)
    layout = rows(row(autogrid([item("attention", when=True), item("quiet", when=False)], cols=1, px=190)), *PAGES[c])
    for name in names(PAGES[c]):
        els[name] = deeper(c) if name == "deeper" else stamp(name)
    for v in els.values():
        v["spec"]["title"] = v["spec"]["title"].replace(f" · {c}", "")   # the tab already says which system this is
    text = json.dumps(els)
    for stray in ("$gpu", "$model", "$ups", "${ups", "$probe"):
        assert stray in VARIABLES.get(c, ()) or stray not in text, f"{c}: a panel refers to {stray}"
    layout = prefixed(layout, f"{c}-")
    for r in layout["spec"]["rows"]:
        r["spec"]["title"] = r["spec"]["title"].replace(f" · {c}", "")
    return {f"{c}-{k}": v for k, v in els.items()}, tab(f"{w.GLYPH[c]} {c}", layout)


def home():
    els, first = everything()
    tabs = [first]
    for c in w.SYSTEMS:
        system_els, system_tab = system(c)
        els.update(system_els)
        tabs.append(system_tab)
    for i, v in enumerate(els.values(), 1):
        v["spec"]["id"] = i
    return dashboard("home", "Home", "Every system at a glance, then a tab for each. Click a system on the board, or its tab, to open it.", ["overview"],
                     [datasource_variable(), *w.VARS], els, {"kind": "TabsLayout", "spec": {"tabs": tabs}})


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="fail unless the committed dashboard matches this generator")
args = parser.parse_args()
dashboard_json = home()
longest = max(len(x) for _, x in exprs(dashboard_json))
assert longest < MAX_QUERY, f"a query of {longest} characters is longer than VictoriaMetrics accepts"
rendered = json.dumps(dashboard_json, ensure_ascii=False, separators=(",", ":")) + "\n"
assert len(rendered.encode()) < MAX_BYTES, f"the dashboard is {len(rendered.encode())} bytes, too large for its ConfigMap"
if args.check:
    assert OUTPUT.read_text() == rendered, "Regenerate the Home dashboard: python3 scripts/grafana/render-home.py"
else:
    OUTPUT.write_text(rendered)
