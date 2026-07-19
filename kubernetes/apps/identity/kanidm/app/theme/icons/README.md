# Theme icons

SVGs served by kanidm out of `/pkg/icons`. The `kanidm-icons` ConfigMap
(`../../kustomization.yaml`) is directory-mounted at `/hpkg/icons`, so every file
here is served at `/pkg/icons/<basename>` — no per-file mount. ConfigMap keys are
basenames, so **filenames must stay unique across `apps/` and `brand/`.**

```
apps/    portal tiles — one per app, fetched by kaniop
brand/   kanidm's own UI marks — referenced by override.css
```

All are single [Tabler Icons](https://tabler.io/icons) glyphs (MIT License) on a
transparent background. Each SVG carries its own `prefers-color-scheme` media
query, so it flips light/dark in sync with the UI: kanidm drives its theme off
the same OS preference, and a referenced SVG honors that query independently of
the page's `data-bs-theme`.

## `apps/` — portal tiles

Referenced by the matching `KanidmOAuth2Client` via `spec.image.url`, pointing at
`https://auth.home.kelch.io/pkg/icons/<name>.svg`. kaniop fetches that URL
in-cluster (the public host resolves via hairpin and its TLS cert verifies) and
stores the bytes in kanidm. kanidm's `validate_is_svg` preserves the embedded
`<style>`, so the adaptive color survives. Glyph stroked on a transparent card
(no tile): `#4f46e5` (light) / `#818cf8` (dark), `#6366f1` static fallback.

| App         | Tabler glyph     |
|-------------|------------------|
| `grafana`   | `chart-line`     |
| `arr-suite` | `movie`          |
| `ops-suite` | `server-cog`     |
| `metamcp`   | `plug-connected` |

## `brand/` — UI marks

Referenced by `override.css` as `url("/pkg/icons/<name>.svg")` (same-origin;
loaded by the browser). Both are the Tabler `key` glyph in the Indigo Aurora
palette: `#4f46e5` (light) / `#a5b4fc` (dark).

| File           | Where                          |
|----------------|--------------------------------|
| `logo.svg`     | login / recover / error hero, with a radial halo |
| `logo-nav.svg` | navbar brand, no halo          |

To add an app icon: drop `apps/<name>.svg` here, list it in
`../../kustomization.yaml`'s `kanidm-icons` generator, and set the client's
`spec.image.url` to `https://auth.home.kelch.io/pkg/icons/<name>.svg`.
