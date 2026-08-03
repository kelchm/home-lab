# LEMON Manuals self-host

Self-host the LEMON/CHARM car-repair-manual archive (lemon-manuals.la, 1.1TB
torrent) on the cluster, then put an MCP server in front of it for LLM access.

## Design decisions (settled 2026-08-02)

- **NAS share `lemon-manuals-k8s-prod`** on Athena Volume 1. `<what>-<consumer>`
  naming follows `backups-k8s-prod`: the suffix marks workload plumbing — NFS-only,
  no DSM user access, not for humans. A category taxonomy for "large immutable
  re-downloadable corpora" was deliberately deferred to the future ZFS NAS
  (candidate names `corpora`/`commons`/`reference` all rejected for now).
- **Data checksum ON** — must match the `media` share: btrfs refuses reflink
  between mismatched checksum states, and the share is populated by reflink.
- **Populate by reflink, never move**: the torrent stays seeding in qBittorrent at
  `/volume1/media/.downloads/torrents/e9dfaf202d2b8b99988d2e87517b7a90eb73ad92/lemon-manuals/`.
  `sudo cp --reflink=always` (cross-subvolume, same volume) clones it into the
  share — instant, zero extra space, independent inodes.
- **NFS export**: `10.32.25.11/.12/.13`, **Read-only**, **Map all users to admin**,
  sys, async, non-privileged ports denied, cross-mount denied. Map-all-to-admin
  bypasses the DSM share ACL (no per-user ACEs exist on this share; pod UIDs have
  no NAS identity); safe because the IP allowlist and server-side RO are the real
  boundaries. RO corpus share ≠ RW media share — `media` needs real per-app UIDs
  for write ownership and hardlinks, this share needs none.
- **Cluster naming is plain `lemon-manuals`** (namespace, app dir, PV, PVC); the
  PV `share:` path is the only place the NAS name appears (precedent: Longhorn
  BackupTarget URL is the only reference to `backups-k8s-prod`).
- **MCP lives in `ai`** with the rest of the MCP fleet (precedent: grafana-mcp in
  `ai`, Grafana in `observability`); lemon-website lives in its own
  `lemon-manuals` namespace.
- **v1 runs the torrent-shipped musl binary off the NFS mount** from a stock
  alpine image — no image build. v2 (with the MCP work) vendors the in-torrent
  source tarball and CI-builds a real image, at which point `bin/` on the share
  is deleted and the share becomes pure data.
- **OIDC**: v1 reuses the `arr-suite` client (Middleware copy in the new
  namespace + one redirect URL added to `oauth2-arr-suite.yaml`). A dedicated
  OAuth2 client is future work alongside the MCP.

## State

Done:

- Share created, populated (`lemon/`, `charm/`, `bin/`), export live.
- Manifests in `kubernetes/apps/lemon-manuals/` (namespace, middleware, static
  PV/PVC, lemon-website HelmRelease + route `lemon.home.kelch.io` on
  gateway-admin) + the `oauth2-arr-suite.yaml` redirect line.
  `flux-local test --enable-helm --all-namespaces --path kubernetes/flux/cluster`
  passes (111 tests).
- Smoke-tested from a throwaway pod (inline NFS mount, uid 65534): mount OK,
  traverse+read OK, `--help` exec OK (exit 0), write correctly refused
  (`Read-only file system`). Note: DSM's share ACL synthesizes mode 777 over
  NFS, so no `chmod +x` on the binary is needed — POSIX modes on this share are
  cosmetic; the export-level RO is what governs.

Deployed via PR #314 (merge `662c17e`, 2026-08-02): pod serves both databases
(~7s index load, ~500Mi RSS), route 302s to kanidm, deep pages and an
`images.mtbl` JPEG verified. Post-merge review findings (grok + CodeRabbit)
triaged: sha256-gated exec and split liveness/readiness probes added; PV
recovery note below; `nodeAffinity` finding dismissed (all three nodes are in
the export allowlist); `backups-k8s-prod` async-write durability logged as a
separate non-lemon decision.

## Operational notes

- **Binary provenance**: the executed musl binary's sha256 is pinned in the
  HelmRelease command (`f4f86ecb…`) and was verified identical between the
  share copy and the qB-checked torrent copy. The vendored source tarball's
  sha256 lives in the private `kelchm/lemon-website` repo README.
- **Retained-PV recovery**: with `persistentVolumeReclaimPolicy: Retain` and a
  fixed `claimRef`, deleting/pruning the PVC leaves the PV `Released` and it
  will not re-bind. Recover with
  `kubectl patch pv lemon-manuals --type json -p '[{"op":"remove","path":"/spec/claimRef/uid"},{"op":"remove","path":"/spec/claimRef/resourceVersion"}]'`
  (also applies to `media-library`).

## Remaining

1. Cleanup on Athena: `sudo rm /volume1/lemon-manuals-k8s-prod/.reflink-test`.

## Phase 2 — MCP (design sketch, not started)

- No public LEMON MCP exists (checked 2026-08-02). Closest prior art:
  `Gonzih/mcp-charm` (TypeScript, MIT, scrapes charm.li live, base URL not
  configurable, 2 commits) — reference, not a dependency.
- v1 tools need no scraping: `index.json` is the complete vehicle tree
  (`{make, years, model, engine, uriPath}`, 142MB lemon + 5MB charm) for
  browse/search; page content fetched over HTTP from
  `lemon-website.lemon-manuals.svc` and converted HTML→markdown.
- v2 option: read `pages.mtbl` directly (zstd-block SSTables; the vendored
  `oxidized-mtbl` crate in the source tarball is the format reference, with
  `get-key`/`dump` examples).
- Deploy in `ai`, register with metamcp (URL-based registry in Postgres, not
  GitOps). If the MCP reads files directly it needs its own static PV/PVC pair
  in `ai` (one PV binds one PVC).
- Server patches worth considering once source is vendored: a JSON search/browse
  API endpoint (upstream hints at title search as a future feature).
