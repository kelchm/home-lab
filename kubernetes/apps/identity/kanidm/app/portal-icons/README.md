# Portal tile icons

App icons shown on the kanidm application portal grid. Each is referenced by the
matching `KanidmOAuth2Client` via `spec.image.url` (a raw.githubusercontent URL);
kaniop downloads it and sets it in kanidm. The repo is public, so the raw URL is
fetchable.

Each icon is a single [Tabler Icons](https://tabler.io/icons) glyph (MIT License),
stroked in indigo `#6366f1` on a transparent background, so it sits directly on
the portal card (no tile). One static color works on both the light and dark
card surfaces (an uploaded image can't be mode-adaptive).

| App         | Tabler glyph     |
|-------------|------------------|
| `grafana`   | `chart-line`     |
| `arr-suite` | `movie`          |
| `ops-suite` | `server-cog`     |
| `metamcp`   | `plug-connected` |
