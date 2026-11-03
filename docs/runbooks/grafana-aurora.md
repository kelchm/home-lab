# Grafana Aurora styling

Aurora is an optional app for styling the official Grafana image. This runbook describes its delivery, compatibility limits, qualification and rollback. Implementation and acceptance status belong in [#1268](https://github.com/kelchm/home-lab/issues/1268).

## Ownership and delivery

The app is three small files in [`kubernetes/apps/observability/grafana/app/aurora`](../../kubernetes/apps/observability/grafana/app/aurora): a ready-to-load AMD `module.js`, its `plugin.json` manifest and app provisioning. It has no package dependencies, compiler, image build, artifact registry or separate release workflow. The theme definition and its compatibility adapter are readable source in `module.js`. Grafana supplies its own runtime libraries.

Kustomize generates a content-hashed ConfigMap. Its name is rewritten into both Helm mounts by `kustomizeconfig.yaml`; this changes the rendered Pod template when any file changes, so Grafana discovers the new manifest on startup. The plugin directory is read-only under `/var/lib/grafana/plugins/homelab-aurora-app`; downloaded datasource plugins retain their existing writable directory. A directory `subPath` excludes kubelet's hidden timestamp tree, which otherwise causes duplicate plugin discovery. The subpath stays fixed for the Pod's lifetime; the hashed reference provides the required restart on edits. App provisioning enables it in organization 1. Only this plugin ID is allowed to load unsigned.

`python3 scripts/grafana/render-aurora.py` generates the whole `plugin.json` manifest and derives its version from the module's SHA-256. Edit metadata in that script rather than hand-editing the generated manifest. Grafana uses the version in its module URL to invalidate the browser cache. CI rejects a stale manifest. This is metadata generation, not a Grafana or JavaScript build.

## Compatibility boundary

Preloading an app and publishing `ThemeChangedEvent` use public Grafana interfaces. Constructing the theme uses **internal `@grafana/data.createTheme`**, and applying an arbitrary constructed theme is not a documented theme-installation contract. The adapter never patches Grafana files, injects CSS selectors or assigns `config.theme2`; Grafana's own ThemeProvider applies the request.

The exact-version allow-list currently contains **13.3.0 only**. An unqualified release skips theme construction and leaves native styling. A missing factory, construction failure or unavailable event API also skips the adapter. Retries back off, with at most four theme requests over three seconds; failure to apply logs a warning. A theme problem must never hold a Grafana security update: upgrade the official image/chart and accept stock styling until the new version is qualified. Restoring Aurora then takes a qualification run, an allow-list edit and another Grafana rollout. The chart-version comment points upgrade reviewers here.

Aurora replaces the appearance of the native **Dark** choice; there is no independent Aurora entry in preferences. Light and other selected themes remain native. A brief stock first paint is possible. Users following System preference can see stock Dark after an OS appearance change during a session; reloading applies Aurora. The adapter handles theme-change events and bounded startup retries, rather than continuously observing every internal theme update.

`grafana.visualDesignRefresh` is a **public-preview toggle, hidden from upstream's configuration docs and disabled by default**. This configuration enables it for the shell used by the accepted design. It restyles native Light and every user, including users with Aurora disabled. The upstream Dark → Visual Refresh (Dark) remapping is marked temporary, so the toggle and that event behavior need rechecking on each Grafana upgrade. Disabling Aurora alone leaves the preview enabled; reverting the whole change also removes the flag.

The first three chart colors are the accepted azure `#4DB0FF`, seafoam `#3FE6C4` and orchid `#C685FF`. Additional series colors remain provisional. Explicit color overrides in existing dashboards are preserved. The signed-out login and initial splash stay stock. This app changes styling; it does not install the proposed Home layout.

Upstream references: [plugin preloading](https://grafana.com/developers/plugin-tools/reference/plugin-json), [theme factory source](https://github.com/grafana/grafana/blob/v13.3.0/packages/grafana-data/src/themes/createTheme.ts), and [theme-change event source](https://github.com/grafana/grafana/blob/v13.3.0/packages/grafana-runtime/src/services/appEvents.ts).

## Verify an edit or Grafana upgrade

Run the dependency-free checks:

```sh
python3 scripts/grafana/render-aurora.py --check
node --test scripts/grafana/aurora.test.mjs
kustomize build kubernetes/apps/observability/grafana/app > /tmp/grafana-rendered.yaml
```

The opt-in browser qualification needs Node 24, Docker, Helm, Kustomize, yq v4 and Playwright. Keep the browser tooling outside the checkout:

```sh
GRAFANA_THEME_TOOLS=$(mktemp -d)
pnpm --dir "$GRAFANA_THEME_TOOLS" add --save-exact playwright@1.64.1
pnpm --dir "$GRAFANA_THEME_TOOLS" exec playwright install chromium
PLAYWRIGHT_MODULE="$GRAFANA_THEME_TOOLS/node_modules/playwright/index.mjs" \
  node scripts/grafana/qualify-aurora.mjs
```

The suite renders the repository's Helm chart and ConfigMaps, recreates kubelet's projected-volume symlinks and loads those files on a pinned official image. It uses an isolated localhost-only instance with a generated temporary administrator, a native TestData chart and a 1 GiB memory limit. The production datasource plugins are preinstalled to check coexistence; no production datasource or OIDC endpoint is used. It tests native chart pixels, theme events and the real keyboard shortcut, throttled startup, app disable/re-enable including the disabled admin page, module version update/rollback in one browser cache/database, injected adapter failures and an unqualified adjacent release using a fresh database. It mounts individual provisioning files, as production does. It additionally enables asset SRI checks; production leaves those off. Only its own containers and volumes are removed; the printed evidence directory retains logs, a screenshot and `report.json`.

`DOCKER`, `HELM`, `KUSTOMIZE` and `YQ` can name explicit executables. `CHROME` can select an existing Chromium executable. `GRAFANA_CHART` can use an already-extracted copy of the chart at the release's version; otherwise the suite downloads that chart. `GRAFANA_THEME_EVIDENCE` selects an output directory. `GRAFANA_IMAGE` selects a candidate official image for upgrade qualification; update the allow-list only as part of qualifying that exact release, regenerate the manifest and rerun. The adjacent-release case is a separate fresh database, not a database-downgrade test.

After a rollout, verify the actual mounts and startup logs, Kanidm sign-in and break-glass login, native dashboard queries and Light/Dark preferences. Local qualification is not production OIDC acceptance. The browser suite covers Chromium; use the same checks when qualifying another browser or touch device, and review accessibility separately.

## Disable or roll back

For a temporary opt-out, an administrator can disable **Homelab Aurora theme** under Administration → Plugins and data → Plugins, then reload open pages. Selecting Light also remains available. The provisioned enabled setting will be restored on a later Grafana restart.

For a durable opt-out, change `aurora/apps.yaml` to `disabled: true` and follow normal review and Flux delivery. The changed ConfigMap name triggers the normal single-replica Grafana rollout; expect its existing brief RWO-volume downtime. Reload browser pages after rollout. This does not alter dashboard definitions, stored theme preferences or datasource configuration. Reverting the change removes the app mounts, allow-list and refresh flag while keeping the official image.

Roll back with a **Git revert through Flux**, rather than `kubectl rollout undo`: an older ReplicaSet can reference a ConfigMap that Flux has already pruned. A Git revert regenerates the matching ConfigMap and Pod template together. An empty mountpoint directory left on the PVC is harmless.
