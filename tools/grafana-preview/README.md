# Grafana observability preview

Local prototype of Home → domain/workload → resource detail, with the accepted Home briefing, review, activity, checks and Machines, seven domain destinations, native Grafana time-series panels and shared identity/context surfaces. It is a review environment, not a production deployment or a qualification claim. Merge and deployment require owner sign-off.

From the repository root, one command assembles recorded evidence, installs clean dependencies with the frozen lockfiles, builds and publishes immutable plugin artifacts, copies native dashboards, and launches three isolated Grafana/fixture pairs:

```sh
python3 tools/grafana-preview/preview.py up --dependencies frozen
```

Docker must already have the official `grafana/grafana:13.3.0-distroless` and `node:24.22.1-alpine` images. The command uses `--no-build --pull never`; there is no custom image or Aurora dependency. Node 24.22.1 and pnpm 12.5.1 are the tested host tools. Local installed binaries are selected directly, without consulting `.mise.toml`. Other checkouts/machines can specify executable paths through `NODE` / `PNPM` or `--node` / `--pnpm`:

```sh
NODE=/path/to/node PNPM=/path/to/pnpm \
  python3 tools/grafana-preview/preview.py up --dependencies frozen \
  --project grafana-observability-preview \
  --ports 3431,3432,3433,3434,3435,3436 \
  --state-dir /tmp/grafana-preview-state --log-dir /tmp/grafana-preview-logs \
  --output-dir /tmp/grafana-preview-builds
```

Ports are checked before startup. Viewer is [3431](http://127.0.0.1:3431/a/homelab-observability-app/explore), test-a [3433](http://127.0.0.1:3433/a/homelab-observability-app/explore), test-b [3435](http://127.0.0.1:3435/a/homelab-observability-app/explore); adjacent even ports are their fixtures. Compose projects, state and logs are separate per project/lane, and ports bind only to localhost. No user preferences, cluster objects, existing comparison containers on 3415/3416, or unrelated containers are changed. `--lanes viewer` launches only the viewer pair. Existing controls persist between launches; tests reset only their own lanes.

`--dependencies cached` is the faster local convenience when the existing `shared/node_modules` and `scenes/node_modules` are available. Both modes build a source snapshot under the generated output. Frozen installs happen in fresh snapshot directories, not the original dependency symlinks; neither mode installs into or modifies their targets. `setup --dependencies frozen` performs only clean frozen-lockfile installation; `build --dependencies frozen` installs/builds/publishes without starting containers. Source snapshots and published artifacts have content-addressed paths. Published `plugin.json` versions contain the source hash to change Grafana's plugin cache key. Each release records module/content hashes in `plugin-lock.json`; old published paths are never overwritten.

Default generated output is `runtime/generated/{cached,frozen}/`. `<project>-lanes.json` there records exact URLs, Compose files, fixture/state/log paths and release identifiers. Use the same dependency/output/project options for `status`, `smoke` and `down`. `down` checks the checkout ownership label before stopping only that project's named lane pairs; no global cleanup is performed.

## Data and navigation

Recorded replay is the default. Daily evidence is 2026-11-01T01:27:04.636Z–2026-11-02T01:27:04.636Z. Inference detail uses a separate, explicitly dated 2026-10-31T23:00Z–2026-11-01T00:05Z window. [Capture provenance](fixtures/README.md) documents originals and hashes. No samples are relabeled to the present. Missing captures and unsupported queries return successful empty Prometheus vectors/matrices, without fabricated zeros. The server has no live upstream.

Live datasource UID `comparison-live` points to `http://host.lima.internal:38428` by default (`--live-url` overrides it). It needs an independently established read-only VM forward. Recorded UID `comparison` points only to the lane's fixture. A live failure never falls back to recorded data. Source switches retain the selected range; direct live entry defaults to a moving 24-hour window.

`assemble` regenerates a catalog from the unchanged base `runtime/catalog.json`, current common/network query definitions, every available `shared/src/domains/*/queries.json`, and capture locators. Initially missing domains are reported in the generated manifest and do not prevent assembly. Named groups are written under `domains` in generated `queries.json`; the app imports sample-free common/network and domain query definitions. Assembly rejects duplicate refIds, clips to the correct source windows, preserves result-level inference metadata for all frames, canonicalizes equal expressions, and records all refId aliases for controls/logs. Disjoint instant capture windows cannot lend one another labels or points. Original captured expressions/hashes remain recorded when a live expression changes; replay does not re-execute the updated rule against old samples. This is a bounded replay lookup, not a PromQL interpreter.

Native dashboard JSON from `kubernetes/apps/observability/grafana/app/` is copied locally, retaining dashboard UIDs. Both v1 and v2 Prometheus query sources use `${datasource}`. Their stable `datasource` variable defaults to `comparison` and accepts `comparison-live` through `var-datasource`; native-to-native links carry that variable. The Home copy is also available under `home-compare-native`. Original repository dashboards remain unchanged. `ctx.deeper(uid, variables)` must build a local `/d/<uid>` URL with `var-datasource=comparison` or `comparison-live`, preserve `from`, `to`, `timezone`, `source`, `focus`, `view`, `entity`, and encode resource variables as `var-*`. The core already supplies this contract.

Only committed native exports in that directory are copied: externally supplied dashboards (for example Drive health / Node Exporter Full) may be absent. Uncaptured native expressions/selector expansions remain empty in replay; label discovery with a selector is deliberately empty rather than returning another system's inventory. Log datasources are not provisioned. These are explicit coverage limits; do not read native empty panels as healthy systems.

## Runtime checks

```sh
python3 tools/grafana-preview/preview.py test
python3 tools/grafana-preview/preview.py smoke --dependencies frozen \
  --evidence-dir /tmp/grafana-preview-browser-evidence
```

Browser checks use separately installed Playwright and its bundled Chromium. Alternatively, set `PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs` and `CHROMIUM=/path/to/chromium`; neither needs installation in shared/scenes. The minimal smoke proves dated Home rendering, failure/empty unknown states, recovery, missing entity handling, test-a/test-b isolation, and effective live-source selection in both native dashboard formats. Live requests are intercepted locally in that smoke. Controls are restored in `finally`; screenshots, scoped requests and `smoke.json` go to the chosen evidence directory outside tracked source.

From `tools/grafana-preview`, run the broader browser checks after `up`:

```sh
# Every domain/subpage, observed resource links, history, palette, and phone layouts.
node checks/browser.mjs
# Sticky navigation, scoped queries, cache/history, inspector and sharing.
BASE=http://127.0.0.1:3435 node scenes/tests/browser/proof.mjs
# Browser-local datasource-resolution failure through the real Scenes runner, then recovery.
node checks/request-failure.mjs
# Opt-in real read-only telemetry, after establishing the VM forward.
node checks/live.mjs
```

`checks/live.mjs` visits each system once over a moving 24-hour window, requires the live datasource on every request, and records query errors and populated/empty results separately. It makes real read-only queries; the default replay checks never require production connectivity. Evidence defaults to ignored `evidence/`; `EVIDENCE` overrides it for the cross-domain and live checks. Browser verification is Chromium only; a complete accessibility audit and real touch-device testing remain outstanding.

Fixture control is a JSON object replacing only that lane's state:

```sh
curl -fsS -X POST http://127.0.0.1:3434/control \
  -H 'Content-Type: application/json' -d '{"failRefs":"^D_spark_"}'
curl -fsS -X POST http://127.0.0.1:3434/control \
  -H 'Content-Type: application/json' -d '{}'
```

Controls: `failRefs`, `emptyRefs`, `gapRefs` and `delayRefs` are refId regular expressions that match every canonical alias; `delayMs` is 0–60000, `status` selects an injected 4xx/5xx, `missingEntity` removes series with that exact label value, and `gapWindow: {from,to}` optionally supplies gap bounds in epoch milliseconds. Defaults use the middle 20% of each capture window for gaps. Client cancellation logs a `cancelled` event and interrupts the delay. `GET /control` reads state; `GET /logs?ref=D_spark_memoryAvailable&phase=done&since=<epoch-ms>` returns at most the latest 1000 matching events with lane, aliases, original request expression, timestamps and capture status. Refresh/reload Grafana after changing controls.
