# Grafana Aurora theme image

## Status and implementation boundary

Production still runs the stock Grafana image selected by its Helm chart. `kubernetes/apps/observability/grafana/image/` builds a candidate with **Homelab Aurora** registered as an independent native theme. Publishing an image does not change the HelmRelease, install the redesigned Home, or change production preferences. [Investigation #1268](https://github.com/kelchm/home-lab/issues/1268) tracks the wider dashboard work.

Grafana has no shipped public API for installing arbitrary themes. This is a downstream source customization: one new theme JSON, an entry in the internal frontend registry, an entry in the native selector, and upstream generation of the backend's known theme IDs. Once registered, Grafana's own startup, preference inheritance, URL overrides and theme context deliver it normally. Stock Dark and Light remain separate choices. There is no runtime theme adapter, injected CSS, global state mutation, retry loop or minified-bundle patch.

`theme.json` is the canonical full native definition for this pinned release, not a new stable theme API. The palette is the accepted Aurora study. Custom dashboard components should consume the same roles when implemented; this image does not yet deliver a panel package.

## Why source registration

The [upstream custom-theme experiment](https://github.com/grafana/grafana/pull/119725) remained an unmerged draft and was closed by the stale bot in May 2026. Its API, storage, import/editor and preference work would be disproportionate to carry for one theme. The [maintainer discussion](https://github.com/grafana/grafana/pull/119725#discussion_r2951416243) also recommends a smaller, more stable user-facing theme model.

The earlier preloaded-app proof used internal `createTheme`, replaced global theme state, published a change event and retried during initialization. A thin image changing one bundled theme literal also worked, but overloaded an existing theme ID and coupled upgrades to minified output. Those experiments are retained in local research artifacts rather than shipped as additional implementations.

The public 13.3.0 source commit is `752e3462ff71a43a5c1fb62d00a482fa2dc0ee6a`; the official image identifies private mirror commit `6f8ce3c693b493043ebcd4787566d2b6b118b0f0` on `release-13.3.0#patched`. Initially that difference appeared to block a safe source build. The [public release commit](https://github.com/grafana/grafana/commit/752e3462ff71a43a5c1fb62d00a482fa2dc0ee6a) includes the ten release security patches, and independent control builds reproduced both official architecture binaries and all 3,321 frontend files exactly. The [release workflow](https://github.com/grafana/grafana/blob/752e3462ff71a43a5c1fb62d00a482fa2dc0ee6a/.github/workflows/release-build.yml#L81) explains the two commit identities. Equal version numbers alone are insufficient; the required build gates below establish equivalence for the specific candidate.

## Required build gates

The Dockerfile first downloads the immutable public commit archive, verifies its SHA-256, checks the exact official image identity/version stamp and installs upstream's frozen dependencies. Before applying any modification it:

1. Builds the pristine frontend, with source maps, and compares every file name and byte against the official image's `public/build`.
2. Builds the pristine backend with the official binary's version, commit, branch and buildstamp, then runs `cmp` against that binary for the target architecture.

Only after both gates pass does it add Aurora, validate the definition through the candidate's own Zod schema and theme factory, regenerate backend IDs, and compile the modified frontend and backend. The final image retains the official runtime's configuration, views, bundled plugins and licenses. It records the actual public source commit, `13.3.0+homelab.1` version, registration hashes and both parity reports under `/usr/share/grafana/homelab-theme/`.

A different public tree, missing patch, compiler mismatch or packaging change stops the build. This does not guarantee future Grafana releases are reproducible. If a candidate cannot reproduce upstream, stay on stock Grafana and resolve the mismatch separately; never hold a security upgrade for the theme.

## Build and qualify

From `kubernetes/apps/observability/grafana/image/`, with Docker and Node 24.22.1 available:

```sh
docker build --platform linux/amd64 -t grafana-aurora:local .
corepack pnpm install --frozen-lockfile --ignore-scripts
corepack pnpm exec playwright install --with-deps chromium
corepack pnpm test
node qualify.mjs grafana-aurora:local
```

The cluster is amd64. Builder stages run natively and Go cross-compiles for the target architecture; omitting `--platform` builds a local arm64 preview on Apple Silicon. `DOCKER` and `CHROMIUM_PATH` can select existing local executables. Heavy compilation stages run sequentially, with Go limited to two workers, to fit a small builder. Grafana's upstream package manager remains its vendored Yarn; pnpm manages only this repository's two browser-test packages.

The qualification creates only its own container, generated credentials and database volume, uses Grafana TestData, and removes those resources on completion. It saves reports and screenshots in ignored `test-results/`. It checks the image's parity receipts, actual source/version, native chart pixels, asset integrity enforcement, independent Aurora selection, stock Dark/Light, organization inheritance, URL overrides, invalid-ID rejection, throttled reloads and phone rendering. The synchronous plugin preinstall setting mirrors the current production configuration, including VictoriaMetrics Metrics and Logs; the report records their installed versions and signatures. This checks preinstallation, not real backend query integration.

It then switches candidate → stock → candidate against the same SQLite database, browser context and HTTP cache. Stock falls back to Dark while preserving the stored Aurora preference; restoring the custom image restores Aurora. This proves same-version artifact rollback, not a downgrade across Grafana database migrations.

The workflow qualifies a local amd64 image on pull requests with a read-only token. On main, a separate caller grants publication permission; the same build/test workflow publishes the exact qualified image under a unique commit/run/attempt tag and records its digest. It never updates Helm values. Use the recorded digest for a separately reviewed adoption change; there is no moving deployment tag. The first hosted run must establish the actual cold build time and disk budget before merging.

## Change colors or upgrade

For colors alone, edit `theme.json`, rebuild and run qualification. The source registration is shared by all native panels and Grafana chrome; no dashboard JSON changes are required. The source/control stages remain reusable for palette changes.

For an upstream upgrade:

1. Resolve the new official image to its exact multi-architecture digest and inspect its version stamp and Go build information. Record the image, build commit, branch and buildstamp in `source-lock.json`.
2. Resolve the public release tag to an immutable commit, verify the public security patch import and record its archive SHA-256 and commit timestamp. Never infer equivalence from matching version numbers.
3. Review the two source registration targets and record their pristine hashes. Review the upstream theme definition/schema changes against `theme.json`; the build uses that release's own schema and factory.
4. Align pinned Node, Go and vendored Yarn versions with the release's build. Review version-format changes, including security-release suffixes, before changing the lock guards.
5. Build on amd64. Require both pristine comparisons, unit tests, real browser qualification and production-shaped plugin preinstall. Review screenshots and retain the report; review Grafana's release notes and migration requirements before adoption.

The archive hash fails closed if GitHub ever changes archive bytes; investigate before re-recording it. Tagged digest-pinned builders remain visible to Renovate, but a compiler update that changes the pristine artifact must not bypass the parity gate. Do not exclude the image directory from Renovate to hide a failed upgrade.

## Adoption and rollback

Adoption is unperformed. A later PR must pin the qualified image digest in the Grafana HelmRelease and verify OIDC, provisioning, existing datasource queries, Explore, alerting and the production resource limit. Existing explicit Dark or Light preferences remain those themes; Aurora is the image default only for users inheriting that default. Users can choose it explicitly in their native preferences.

Do not copy `GF_USERS_DEFAULT_THEME=homelab_aurora` into Helm configuration. Activation belongs to the custom artifact; stock Grafana cannot boot with an unknown configured default ID even though its health endpoint may pass. Rolling back the image removes that setting. A stored user/org preference is different: stock safely falls back without deleting the value. A version downgrade still requires Grafana's normal database backup/restore procedure.

Local Chromium qualification is not a complete accessibility audit or a Firefox, Safari, touch-hardware, OIDC or Kubernetes delivery test. The broader native/Scenes comparison remains useful design evidence; production Home is a separate implementation decision.

## Source and notices

The image preserves Grafana's license files and includes `NOTICE.md`, the canonical theme, lock, registration and build scripts, source manifest and parity reports. The revision label identifies the exact home-lab commit needed to reproduce the modification. Keep that source available alongside any published modified image.
