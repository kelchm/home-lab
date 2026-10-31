#!/usr/bin/env python3
"""Render the NTP dashboard: the UPS Pis' time service, from each server's state to the sources behind it.

Servers come from a fixed list rather than from what reports, so a Pi that goes quiet stays on the page and says so.
Each current-state tile has a value to fall back on for the same reason. The schema is shared with the Home dashboard.
"""
import argparse
import json
from pathlib import Path

from home.schema import (FINE, LOOK, NEUTRAL, OTHER, QUIET, SERIES, TS, Panels, by_re, dashboard, datasource_variable, exprs, fixed, fixed_list, grid, q, repeated, row, rows, th, tile_row,
                         ts_custom, union, words)

OUTPUT = Path(__file__).resolve().parents[2] / "kubernetes/apps/observability/grafana/app/ntp-dashboard.json"
SERVERS = ["ups-compute-rack", "ups-office"]
SEL = 'job="pi-chrony",instance=~"$server"'
DISH = "192.168.100.1"
LOCAL = "127.127.1.1"
SILENT_AFTER = 300   # seconds without a sample; scrapes run every 30 s
HOLDOVER_AFTER = 3600   # the reference age NTPNotSynchronized fires on
ERROR_LIMIT = 1   # the estimate NTPClockErrorHigh fires on
MAX_QUERY = 16_000   # VictoriaMetrics rejects a query over 16,384 characters

LAST_SAMPLE = f"max(tlast_over_time(up{{{SEL}}}[1h]))"
ERROR = f"abs(chrony_tracking_system_time_seconds{{{SEL}}}) + chrony_tracking_root_dispersion_seconds{{{SEL}}} + 0.5 * chrony_tracking_root_delay_seconds{{{SEL}}}"
REFERENCE_AGE = f"time() - chrony_tracking_reference_timestamp_seconds{{{SEL}}}"
STRATUM = f"max(chrony_tracking_stratum{{{SEL}}})"
PUBLIC = f'chrony_sources_reachability_ratio{{{SEL},source_address!={json.dumps(DISH)},source_address!={json.dumps(LOCAL)}}}'
BARE = {"color": fixed(OTHER)}


def fresh(expr):
    """Only while the server's scrape is still arriving."""
    return f"({expr}) and on () (time() - {LAST_SAMPLE} < {SILENT_AFTER})"


def known(expr):
    """A value that says it is unknown once the server goes quiet: with no series at all, a tile would lose its name too."""
    return f"({fresh(expr)}) or vector(-1)"


def upstream():
    """What each server tracks: 1 the dish, 2 a public source, 3 its own clock, 0 nothing. An unsynchronized server reports no address."""
    e = f'chrony_tracking_info{{{SEL}}}'
    for code, pattern in ((2, ".+"), (0, ""), (1, DISH), (3, LOCAL)):
        e = f'label_replace({e}, "code", "{code}", "tracking_address", "{pattern}")'
    return f'max by (instance) (label_value({e}, "code"))'


def named(expr):
    """The dish by name; a public source has only its address."""
    return f'label_replace(label_replace({expr}, "source", "$1", "source_address", "(.*)"), "source", "Starlink dish", "source_address", "{DISH}")'


def source(metric):
    return f"max by (source) ({named(f'{metric}{{{SEL}}}')})"


# chrony's own words for the source states, in order from selected to unusable; the dashboard's words are in STATE.
CODES = ", ".join(f'"{s}", "{i}"' for i, s in enumerate(("sync", "candidate", "outlier", "jittery", "falseticker", "unreach"), 1))
SOURCE_STATE = f'max by (source) (label_value(label_map({named(f"max_over_time(chrony_sources_state_info{{{SEL}}}[$__rate_interval])")}, "source_state", {CODES}), "source_state"))'
STATE_NOW = f'max by (source) (label_value(label_map({named(f"chrony_sources_state_info{{{SEL}}}")}, "source_state", {CODES}), "source_state"))'
STATE = words(_1=("Selected", "#C7CDD8"), _2=("Combined", "#9DA5B4"), _3=("Standby", "#5C6370"), _4=("Jittery", "orange"), _5=("Rejected", "orange"), _6=("Unreachable", LOOK))
UPSTREAM = words(_1=("Dish", FINE), _2=("Public", "#C7CDD8"), _3=("Local", LOOK), _0=("None", "red"))
REACH = [{"type": "value", "options": {"8": {"text": "All 8 answered", "color": "#5C6370", "index": 0}, "0": {"text": "None answered", "color": "red", "index": 1}}},
         {"type": "range", "options": {"from": 1, "to": 7, "result": {"text": "Some missed", "color": LOOK, "index": 2}}}]

POLLS = {str(n): f"{n}/8" for n in range(9)}
UNKNOWN = {"-1": ("–", NEUTRAL)}

P = Panels()


def line(key, title, series, unit, desc, lo=None, limit=None, curve="stepAfter", colours=tuple(zip(SERVERS, SERIES)), least=None):
    """History of one measure. Chrony updates these every few minutes, so a value holds until the next update unless curve says otherwise."""
    d = {"unit": unit, **BARE, "custom": ts_custom(lineInterpolation=curve, fillOpacity=0, spanNulls=False)}
    if lo is not None:
        d["min"] = lo
    if least:
        d["custom"]["axisSoftMax"] = least   # a counter at zero would otherwise autoscale to 100
    if limit:
        d["thresholds"] = th([{"value": None, "color": "transparent"}, {"value": limit, "color": "orange"}])
        d["custom"]["thresholdsStyle"] = {"mode": "line"}
    overrides = [by_re(f"/^{n}/", color=fixed(c)) for n, c in colours]
    if len(series) > 1:
        overrides.append(by_re("/ RMS$/", custom__lineStyle={"fill": "dash", "dash": [8, 6]}))
    return P.add(key, title, "timeseries", [q(e, leg, r, instant=False) for (leg, e), r in zip(series, "ABCD")], TS, d, overrides, desc=desc)


def timeline(key, title, expr, legend, mappings, desc):
    return P.add(key, title, "state-timeline", [q(expr, legend, instant=False)],
                 {"alignValue": "left", "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}, "mergeValues": True, "rowHeight": 0.82, "showValue": "never", "tooltip": {"mode": "single"}},
                 # Threshold colouring would merge the values by threshold and lose the mappings, which carry each state's word and colour.
                 {"unit": "short", "color": fixed("#5C6370"), "mappings": mappings, "custom": {"fillOpacity": 90, "lineWidth": 0, "spanNulls": False}},
                 desc=desc)


# ------------------------------------------------------------------ each server now
PROBLEM_AFTER = th(QUIET + [{"value": HOLDOVER_AFTER, "color": "orange"}])
P.tiles("server", "Time server · ${server}", [
    ("Collection", union(f"(time() - {LAST_SAMPLE} >= {SILENT_AFTER}) * 0", f"(max(up{{{SEL}}}) == 0) * 0 + 1", f"(max(chrony_up{{{SEL}}}) == 1) * 0 + 3", f"(max(up{{{SEL}}}) == 1) * 0 + 2", "vector(0)"),
     dict(mappings=words(_3=("Complete", None), _2=("Partial", None), _1=("Failing", None), _0=("Silent", None)), thresholds=th([{"value": None, "color": "red"}, {"value": 2, "color": "orange"}, {"value": 3, "color": NEUTRAL}]))),
    ("Sync", f"WITH (stratum = ({fresh(STRATUM)}), age = (max({REFERENCE_AGE}))) " + union("((stratum == 0) or (stratum >= 16)) * 0 + 1", f"(stratum and on () (age > {HOLDOVER_AFTER})) * 0 + 2", f"(stratum and on () (age <= {HOLDOVER_AFTER})) * 0 + 3", "vector(0)"),
     dict(mappings=words(_3=("Synced", None), _2=("Holdover", None), _1=("Unsynced", None), _0=("Unknown", None)), thresholds=th([{"value": None, "color": "red"}, {"value": 2, "color": "orange"}, {"value": 3, "color": NEUTRAL}]))),
    ("Upstream", known(f'max({upstream()})'), dict(mappings=words(**{"-1": ("Unknown", None)}, _1=("Dish", None), _2=("Public", None), _3=("Local", None), _0=("None", None)), thresholds=th([{"value": None, "color": NEUTRAL}, {"value": 0, "color": "red"}, {"value": 1, "color": NEUTRAL}]))),
    ("Stratum", known(STRATUM), dict(mappings=words(**UNKNOWN), thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": NEUTRAL}, {"value": 16, "color": "red"}]))),
    ("Last clock update", known(f"max({REFERENCE_AGE})"), dict(unit="s", mappings=words(**UNKNOWN), thresholds=PROBLEM_AFTER)),
    ("Estimated error", known(f"max({ERROR})"), dict(unit="s", decimals=1, mappings=words(**UNKNOWN), thresholds=th(QUIET + [{"value": ERROR_LIMIT, "color": "orange"}]))),
    ("Dish reach", f'({fresh(f"8 * max(chrony_sources_reachability_ratio{{{SEL},source_address={json.dumps(DISH)}}})")}) or vector(-1)',
     dict(mappings=words(**{"-1": ("Missing", None)}, **POLLS), thresholds=th([{"value": None, "color": "orange"}, {"value": 1, "color": LOOK}, {"value": 8, "color": NEUTRAL}]))),
    ("Public sources", known(f"sum({PUBLIC} > bool 0)"), dict(mappings=words(**UNKNOWN), thresholds=th([{"value": None, "color": NEUTRAL}, {"value": 0, "color": LOOK}, {"value": 1, "color": NEUTRAL}])))],
    wide=True, desc="Collection: Partial means the exporter could not read all of chrony's data, even if tracking looks healthy; Silent means no sample for five minutes. Sync: Holdover is no clock update for an hour, and Unknown is no current tracking data. "
    "These follow NTPNotSynchronized and NTPClockErrorHigh without their waiting periods. Dish reach counts the last 8 polls; Public sources counts those that answered any of theirs. The estimate assumes the upstream is right; it is not calibrated UTC accuracy.")

# ------------------------------------------------------------------ the clock over time
line("error", "Estimated clock error", [("{{instance}}", ERROR)], "s", "Chrony's estimate of its system clock error, as NTPClockErrorHigh reads it: system time offset, plus root dispersion, plus half the root delay. The alert line is 1 s. "
     "It assumes the upstream is right; it is not calibrated UTC accuracy.", lo=0, limit=ERROR_LIMIT)
line("offset", "Offset from upstream", [("{{instance}}", f"chrony_tracking_last_offset_seconds{{{SEL}}}"), ("{{instance}} RMS", f"chrony_tracking_rms_offset_seconds{{{SEL}}}")], "s",
     "The estimated offset at each clock update, and its long-term RMS, dashed. Both are relative to the upstream, not to UTC, so a small offset shows steady tracking rather than accuracy.")
line("update-age", "Time since last clock update", [("{{instance}}", REFERENCE_AGE)], "s", "Rises until the next update, so a sawtooth is normal. NTPNotSynchronized treats an hour without one as holdover, which is the alert line.",
     lo=0, limit=HOLDOVER_AFTER, curve="linear")
line("root-delay", "Root delay", [("{{instance}}", f"chrony_tracking_root_delay_seconds{{{SEL}}}")], "s", "Network path delay to the stratum 1 clock, through the upstream. It steps when the upstream changes.", lo=0)
line("root-dispersion", "Root dispersion", [("{{instance}}", f"chrony_tracking_root_dispersion_seconds{{{SEL}}}")], "s", "Uncertainty accumulated back to the stratum 1 clock. It grows between updates and resets at each one.", lo=0)
line("frequency", "Clock frequency error", [("{{instance}}", f"chrony_tracking_frequency_ppms{{{SEL}}}")], "ppm", "How fast the Pi's own oscillator runs compared with true time. Slow drift follows temperature; a step suggests a bad source.")
line("skew", "Frequency uncertainty", [("{{instance}}", f"chrony_tracking_skew_ppms{{{SEL}}}")], "ppm", "Chrony's error bound on that frequency. It climbs when its measurements disagree, often with a noisy upstream.", lo=0)
timeline("upstream", "Upstream over time", upstream(), "{{instance}}", UPSTREAM,
         "What each server tracked. Moving between the dish and public sources is ordinary source selection, not an outage; None means it was unsynchronized. A gap means no data. Chrony can hold a lost source for hours, so compare reachability below.")

# ------------------------------------------------------------------ requests served
P.note("packets", "**Packets are not clients:** this is load, not proof that any client is synchronized.")
line("requests", "NTP requests served", [("{{instance}}", f"3600 * rate(chrony_serverstats_ntp_packets_received_total{{{SEL}}}[1h])")], "suffix:/h", "Valid NTP requests received, per hour, averaged over the past hour so that a client polling every few minutes still reads as a steady rate. "
     "Zero means nothing asked this server; it says nothing about its health. Counter resets are handled.", lo=0, least=1)
line("drops", "NTP requests dropped", [("{{instance}}", f"3600 * rate(chrony_serverstats_ntp_packets_dropped_total{{{SEL}}}[1h])")], "suffix:/h", "Requests chrony dropped through rate limiting, per hour, averaged over the past hour. These counters cannot name a client or prove one is synchronized; check the client itself.", lo=0, least=1)

# ------------------------------------------------------------------ each server's sources
SECONDS = dict(unit="s", decimals=1)
DISH_LINE = (("Starlink dish", SERIES[2]),)
P.table("sources", "Now", "source", "Source", [
    ("State", STATE_NOW, dict(mappings=STATE, custom__cellOptions={"type": "color-text"}, custom__width=120)),
    ("Reach", f'8 * {source("chrony_sources_reachability_ratio")}', dict(mappings=words(**POLLS), custom__cellOptions={"type": "color-text"}, thresholds=th([{"value": None, "color": "red"}, {"value": 1, "color": LOOK}, {"value": 8, "color": "text"}]))),
    ("Last sample", source("chrony_sources_last_sample_age_seconds"), dict(unit="s")),
    ("Polled every", source("chrony_sources_polling_interval_seconds"), dict(unit="s")),
    ("Stratum", source("chrony_sources_stratum"), {}),
    ("Offset", source("chrony_sources_last_sample_offset_seconds"), SECONDS),
    ("Error margin", source("chrony_sources_last_sample_error_margin_seconds"), SECONDS),
    ("Delay", source("chrony_sources_peer_delay_seconds"), SECONDS),
    ("Dispersion", source("chrony_sources_peer_dispersion_seconds"), SECONDS)],
    sort="State", desc_sort=False, keywidth=150, desc="Every source chrony is polling. Selected is the one it tracks (chrony's sync, *). Standby is chrony's outlier (-): usable and answering, not broken. Rejected is a falseticker. Reach is how many of the last 8 polls answered. "
    "Offset is that source against this server's clock at its last sample, with chrony's margin for that sample; delay and dispersion come from the source's last reply.")
P.elements["sources"]["spec"]["vizConfig"]["spec"]["fieldConfig"]["defaults"]["noValue"] = "No current data"
timeline("source-states", "Selection over time", SOURCE_STATE, "{{source}}", STATE,
         "Each source's state over time; the worst seen wins at each point. Selection moving from the dish to a public source is the fallback working. A row ending partway is a public source that left the pool.")
timeline("source-reach", "Reachability over time", f'min by (source) (8 * {named(f"min_over_time(chrony_sources_reachability_ratio{{{SEL}}}[$__rate_interval])")})', "{{source}}", REACH,
         "How many of each source's last 8 polls answered. A poll can be over 17 minutes apart, so one lost packet stays visible here for over two hours.")
line("source-offsets", "Offsets", [("{{source}}", source("chrony_sources_last_sample_offset_seconds"))], "s", "Each source against this server's clock at its last sample. Sources that disagree usually show path asymmetry, so public offsets here are not errors in this server's clock.",
     colours=DISH_LINE)
line("source-delays", "Delays", [("{{source}}", source("chrony_sources_peer_delay_seconds"))], "s", "Round-trip delay to each source. Rising dish delay can push selection to a public source.", lo=0, colours=DISH_LINE)
for key in ("source-offsets", "source-delays"):
    P.elements[key]["spec"]["vizConfig"]["spec"]["fieldConfig"]["overrides"][0]["properties"].append({"id": "custom.lineWidth", "value": 3})


def stacked(*lines):
    """Lines of unequal panels, one above another, each given as (height, *cells)."""
    items, y = [], 0
    for h, *cells in lines:
        for it in grid(h, *cells)["spec"]["items"]:
            it["spec"]["y"] = y
            items.append(it)
        y += h
    return {"kind": "GridLayout", "spec": {"items": items}}


LAYOUT = rows(
    tile_row(P.groups["server"], "server"),
    row(grid(8, ("error", 8), ("offset", 8), ("update-age", 8))),
    row(grid(5, ("upstream", 24))),
    row(grid(7, ("root-delay", 6), ("root-dispersion", 6), ("frequency", 6), ("skew", 6))),
    row(grid(2, ("packets", 24)), "Requests"),
    row(grid(7, ("requests", 12), ("drops", 12))),
    repeated(row(stacked((7, ("sources", 24)), (9, ("source-states", 12), ("source-reach", 12)), (8, ("source-offsets", 12), ("source-delays", 12))), "Sources · ${server}"), "server"))
for i, v in enumerate(P.elements.values(), 1):
    v["spec"]["id"] = i


def link(title, url, external=False):
    return {"title": title, "type": "link", "icon": "doc" if external else "dashboard", "tooltip": "", "url": url, "tags": [], "asDropdown": False, "targetBlank": external, "includeVars": False, "keepTime": not external}


RUNBOOKS = "https://github.com/kelchm/home-lab/blob/main/docs/runbooks"
DASHBOARD = dashboard("ntp", "NTP", "The UPS Pis' time service: each server's state, its clock over time, requests served and its sources. Shows 24 hours by default because chrony polls and updates only every few minutes.",
                      ["network", "ntp"], [datasource_variable(), fixed_list("server", SERVERS)], P.elements, LAYOUT, start="now-24h",
                      links=[link("Hosts", "/d/hosts"), link("Server runbook", f"{RUNBOOKS}/host-monitoring.md#local-time-service", True), link("Client runbook", f"{RUNBOOKS}/local-ntp.md", True)])

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="fail unless the committed dashboard matches this generator")
args = parser.parse_args()
longest = max(len(x) for _, x in exprs(DASHBOARD))
assert longest < MAX_QUERY, f"a query of {longest} characters is longer than VictoriaMetrics accepts"
rendered = json.dumps(DASHBOARD, ensure_ascii=False, separators=(",", ":")) + "\n"
if args.check:
    assert OUTPUT.read_text() == rendered, "Regenerate the NTP dashboard: python3 scripts/grafana/render-ntp.py"
else:
    OUTPUT.write_text(rendered)
