# Homelab Aurora source modification

Modified 2026-11-02 for the homelab. This image rebuilds Grafana 13.3.0 from the immutable public source in `source-lock.json`, adding a separate Aurora theme through the frontend registry, native selector and generated backend theme IDs. It enables the theme through image configuration. Grafana's original licenses remain in the image.

The pristine frontend and backend must reproduce the official distribution byte for byte before modification. The official mirror commit and public source commit differ; the build does not assume equal version numbers establish equivalence. Both parity reports and the actual source/version information are included under `/usr/share/grafana/homelab-theme/`.

Corresponding upstream source: <https://github.com/grafana/grafana/tree/752e3462ff71a43a5c1fb62d00a482fa2dc0ee6a>.

Modification source and build instructions: <https://github.com/kelchm/home-lab/tree/main/kubernetes/apps/observability/grafana/image>. For an individual artifact, use the exact home-lab commit in its `org.opencontainers.image.revision` label rather than moving `main`.

The [runbook](https://github.com/kelchm/home-lab/blob/main/docs/runbooks/grafana-theme.md) describes reconstruction, qualification, upgrade and rollback. If pristine parity fails, retain stock Grafana rather than delaying a security upgrade for the theme.
