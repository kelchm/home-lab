# Portal tile icons

App icons shown on the kanidm application portal grid. Each is referenced by the
matching `KanidmOAuth2Client` via `spec.image.url` (a raw.githubusercontent URL);
kaniop downloads it and sets it in kanidm. The repo is public, so the raw URL is
fetchable.

Each icon is a cohesive indigo tile (matching the Indigo Aurora theme) with a
glyph from [Tabler Icons](https://tabler.io/icons) (MIT License):

| App         | Tabler glyph     |
|-------------|------------------|
| `grafana`   | `chart-line`     |
| `arr-suite` | `movie`          |
| `ops-suite` | `server-cog`     |
| `metamcp`   | `plug-connected` |
