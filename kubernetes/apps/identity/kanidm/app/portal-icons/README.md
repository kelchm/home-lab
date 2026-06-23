# Portal tile icons

App icons shown on the kanidm application portal grid. Each is referenced by the
matching `KanidmOAuth2Client` via `spec.image.url` (a raw.githubusercontent URL);
kaniop downloads it and sets it in kanidm. The repo is public, so the raw URL is
fetchable.

Each icon is a single [Tabler Icons](https://tabler.io/icons) glyph (MIT License)
stroked on a transparent background, so it sits directly on the portal card (no
tile). The SVG carries a `prefers-color-scheme` media query, so the glyph color
adapts to light/dark — `#4f46e5` (light) / `#818cf8` (dark), with a `#6366f1`
static fallback. kanidm drives its own theme off the same OS preference, and an
`<img>`-rendered SVG honors that query, so the icon flips in sync with the UI.
(kanidm validates then serves the SVG bytes unchanged, so the embedded `<style>`
survives — verified against the server's `validate_is_svg`.)

| App         | Tabler glyph     |
|-------------|------------------|
| `grafana`   | `chart-line`     |
| `arr-suite` | `movie`          |
| `ops-suite` | `server-cog`     |
| `metamcp`   | `plug-connected` |
