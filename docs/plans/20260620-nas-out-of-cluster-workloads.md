# Out-of-cluster workloads on the Synology — S3 DB-backup target

**Status:** Proposed 2026-06-20. Research + recommendation; nothing implemented. This doc decides *where*, *what*, and *how* for workloads that must live outside the Talos cluster, using the first concrete one — a clean S3 DB-backup target — as the worked example. The management pattern is meant to generalize to future out-of-cluster services.

## Problem

Some workloads need to run outside `k8s-prod`. The motivating case is **S3-compatible object storage as a clean database-backup target**: the store that holds backups of the cluster must not depend on the cluster (or Longhorn) it protects, or a cluster/storage incident takes the recovery path down with it. The NAS is the obvious host — the DS1821+ has 32 GB RAM sitting idle and ample free space on the Exos pool.

Today a handful of containers run on the NAS under Dockge, which collapsed into click-ops; no workflow ever felt right. The goal is GitOps, or close enough to it, that fits the repo's existing Flux-webhook + Renovate + SOPS + mise muscle memory. A secondary question — is Docker even the right substrate for object storage? — is answered below: yes, *if* the engine stores objects as plain files.

## Bottom line

Run the S3 target as a **single `versitygw` (Versity S3 Gateway) container in DSM Container Manager**, on storage VLAN 25 at `10.32.25.6`, deployed from this repo by **doco-cd** — a small, maintained Git-driven deploy daemon (declarative, pull-based via poll+webhook, SOPS-native) — *not* a hand-rolled script, not Komodo, not a second Kubernetes. Protect the bucket data with **three immutable tiers**: versitygw S3 Object Lock (COMPLIANCE), DSM immutable Btrfs snapshots, and an off-site Backblaze B2 copy with Object Lock.

The whole design optimizes one property above all: **a backup-of-record must be recoverable without the software, the cluster, or the laptop that created it.** versitygw stores every object as a plain file at its key path, so a full restore can read the backups straight off the Btrfs share (or off B2) with the gateway stopped, removed, or never reinstalled.

## The three decisions

### 1. Where to run — DSM Container Manager (not a VM, not k3s)

| Option | Verdict | Why |
|---|---|---|
| **DSM Container Manager** | **Chosen** | Genuine Docker Engine 24.x + Compose v2. A "Project" is just a `compose.yaml` on a `/volume1` path; `docker compose up -d` works over SSH, so git drives it and the click-ops UI becomes inspection-only. Lightest substrate that stays **filesystem-transparent** — bind-mount the object tree onto a plain Btrfs share and DSM snapshots/replication/Hyper Backup can introspect the actual backups. |
| VMM Debian guest | Rejected (credible runner-up) | The hard requirement is isolation from the *cluster*, which any NAS process already meets — a VM only adds isolation from *DSM itself*. That buys a second OS to patch and a hypervisor lifecycle, and it **breaks filesystem transparency**: objects would live inside an opaque VM disk image, un-introspectable by DSM tooling and un-`ls`-able at 3am without booting the guest. Recovering transparency via loopback-NFS re-export re-adds the moving parts the VM removed. Adopt only if a concrete DSM-coupling threat materializes; the "survive a DSM failure" scenario is instead defended at the data layer by the off-site B2 copy. |
| Second single-node k3s + Flux | Rejected | Exact tooling reuse, but adds a recurring Kubernetes EOL / no-skip-minor upgrade treadmill **plus** Synology kernel-module fragility (k3s on DSM needs containerd snapshotter + cgroup modules that DSM updates can delete), and inserts a k3s control plane + SQLite datastore as a new local failure layer gating the backup workload — all for one long-lived stack, with no isolation gain over a plain container. |
| Bare-DSM service / SynoCommunity package | Rejected | DSM 7.3 *does* ship a customized systemd, but hand-edited units are unsupported and get wiped by DSM updates. The SynoCommunity MinIO package is frozen at the Oct-2022 last-FS-mode release. Neither is a maintainable declarative target. |

A committed Compose Project with `restart: unless-stopped` is the update-survivable substrate: it self-restarts across reboots and DSM upgrades, and all state lives in git + bind mounts.

### 2. What S3 engine — versitygw (not MinIO, not Garage)

Three hard properties for a backup-of-record on this box: **filesystem transparency**, **immutability (WORM)**, and **low-ops / no-wedge** (nothing that can hang mid-incident). Only versitygw satisfies all three.

| Engine | Verdict | Why |
|---|---|---|
| **versitygw** | **Chosen** | Apache-2.0, actively maintained (v1.5.0, 2026-06-02; repo **not** archived). Fronts a POSIX filesystem: a bucket is a top-level directory, object key `cnpg/prod/base/20260620.tar` becomes exactly that file path on the Btrfs volume, multipart assembles into one plain file. Real S3 **Object Lock COMPLIANCE-mode WORM** (immutable even to root) + legal hold. Stateless — no external DB/etcd/quorum; durability == the Btrfs pool. Speaks core S3 + multipart, which is all CNPG/Barman-cloud, pgBackRest, restic, Kopia, and Velero's S3 plugin need. |
| MinIO | Rejected | Object Lock works in the OSS server, but the **community edition went maintenance-mode (README "no longer maintained", Dec 2025)**, the repo is archived, no prebuilt binaries/images since Oct 2025 (source-only), the management console was stripped from the AGPL edition in May 2025, and its `xl.meta` erasure-shard format is opaque (defeats transparency). The Dec-2025 "AIStor Free" tier is proprietary/license-gated, not an OSS path. You do not build a backup-of-record on an archived project. |
| Garage | Rejected | The most aesthetically aligned (single Rust binary, no external DB), but **no Object Lock and no versioning through v2.3.0** (both WIP), so it cannot provide immutable backups; opaque SQLite-metadata + content-addressed blocks fail transparency; and decisively, its own docs warn that single-node `replication_factor=1` can **corrupt the single LMDB metadata copy unrecoverably on unclean shutdown** — disqualifying for the box holding your last copy. |
| SeaweedFS | Fallback only | Apache-2.0 and *does* have Object Lock + versioning — the named fallback **if** a concrete versitygw S3-compatibility gap surfaces with a specific backup tool. Costs: opaque needle-volume format (loses transparency) and a master+volume+filer multi-role architecture (more ops). |
| Ceph/RGW, RustFS | Rejected | Ceph is absurd on one node. RustFS self-declares "do not use in production." |

**This answers the "is Docker even right for object storage?" question:** for a *native erasure-coded* store the answer would be "it deserves its own host" — but versitygw isn't that. It's a thin stateless translation layer over a POSIX directory, and a container is the ideal shape for it. Redundancy comes from the layers *around* it (RAID + snapshots + replication + off-site), not from the engine.

### 3. How to manage — doco-cd (Git-driven deploy), not a script and not a second k8s

| Option | Verdict | Why |
|---|---|---|
| **doco-cd** (`kimdre/doco-cd`) | **Chosen** | A small, maintained Go daemon (distroless, ~v0.94) that watches this repo (poll **and** webhook) and deploys `synology/s3/` on change — declarative, versioned, pull-based, with **native SOPS/age** decryption at deploy time, image-update detection, and Prometheus metrics (which the external monitor can scrape). It is git-as-source-of-truth without bespoke glue — a real tool, not a hand-rolled `bash` loop. It runs as one container in Container Manager and manages native bind-mounted containers, so versitygw's filesystem transparency is preserved. |
| Hand-rolled `git pull` + `compose up` script | Fallback | The original idea (a `sops exec-env`-wrapped `git reset --hard` + `docker compose up -d --remove-orphans` on a DSM Task Scheduler timer, modeled on `apply-media-acls.sh`). Kept as a zero-dependency break-glass fallback if doco-cd's pre-1.0 status ever bites — but as the primary path it *is* the bespoke glue we're trying to avoid. |
| Single-node Talos/k3s + Flux (in a VM) | Rejected | The **only** option that delivers *true* GitOps — continuous reconciliation with drift correction — because the k8s control loop does it for you. But that sole advantage is worth ≈ nothing here: the out-of-cluster set is "S3 + maybe one infra service" (set-once, no drift to correct), another Kubernetes control plane adds no useful failure diversity, and it puts an etcd-can-brick-on-reboot upgrade treadmill on the host that holds your last-resort backups. See note below. |
| Komodo | Rejected (promotion path) | Despite the framing, it is **git-triggered redeploy + a Mongo/FerretDB-backed control plane**, not a reconciler — an open keystone bug ([#1120](https://github.com/moghtech/komodo/issues/1120)) means a webhook sync detects the change and won't even auto-redeploy. It earns its keep only as a **multi-host fleet plane** (reported to struggle past ~200 containers), which this isn't. Revisit if out-of-cluster workloads ever sprawl across several hosts. |
| Portainer CE / Dockge / Nomad / Podman-quadlets | Rejected | Portainer CE paywalls webhooks/force-redeploy; Dockge has no GitOps model (the click-ops trap); Nomad asks a Flux operator to abandon their best tool for Consul/Vault + IBM-governance risk; quadlets give *local* systemd self-heal, not git-driven reconciliation. None wins for a small, static, resilience-tier set. |

**Why not a true-GitOps platform (k8s + Flux)?** The only thing k8s+Flux adds over doco-cd is continuous reconciliation/drift-correction, and that property's value scales with how often a workload *changes* and how many hands touch it. The NAS's architectural niche — independent of both `k8s-prod` and the future PVE cluster — is inherently break-glass infra and backup targets: **set-once-and-forget**, with essentially no drift to correct. So the missing principle is worth ≈ nothing here, while another Kubernetes control plane would add a perpetual upgrade clock and a new failure layer on the very host of last resort. doco-cd keeps the parts of GitOps that *do* matter for this class — declarative, versioned, pulled, SOPS — and drops the reconcile loop these workloads never exercise. App GitOps lives on Kubernetes, where the reconcile loop earns its keep; guest IaC lives with PVE, where an explicit apply is the safer boundary.

**Honest tradeoff:** doco-cd is change-reactive, not continuously self-healing (an out-of-band manual change to a running container isn't stomped back). For a write-once backup target that is the right trade. doco-cd is also pre-1.0 — but it sits only in the *deploy* path: versitygw runs `restart: unless-stopped` and the data is plain files + off-site B2, so a doco-cd failure never threatens the running service or the backups, and the hand-rolled script is the fallback.

## Architecture

### Network, IP, DNS, TLS

- **IP:** `10.32.25.6` on VLAN 25 — the first free address in the architecture doc's already-reserved `.6-.10` "Storage providers (NAS units, MinIO, backup appliances)" block (`nas-storage` is `.5`). The IP plan anticipated exactly this. Cluster clients reach it over the 10GbE SFP+ fabric, fully off the cluster's compute path.
- **Container networking:** give the container its own VLAN-25 identity via a `macvlan` Docker network bound to the NAS's VLAN-25 interface (parent = the bond/eth carrying VLAN 25). Caveat: macvlan imposes host↔container same-segment isolation; the NAS host can't reach its own macvlan container directly — irrelevant here since clients and the off-site `rclone` sidecar reach it fine. Simpler fallback if macvlan-on-DSM proves fiddly: bind versitygw to the host's existing `10.32.25.5` on a dedicated port and skip the distinct `.6` identity.
- **DNS:** `s3-storage.home.kelch.io → 10.32.25.6`, following the flat namespace + `-storage` suffix convention already used for `nas-storage.home.kelch.io`. (Do **not** invent an `s3.storage.kelch.io` hierarchy — the DNS plan is deliberately flat.)
- **TLS:** issue an independent Let's Encrypt cert for `s3-storage.home.kelch.io` via `acme.sh` DNS-01 against Cloudflare, reusing the existing SOPS-encrypted CF-token model, terminated at versitygw directly (no extra reverse proxy). Issue it independently rather than copying the cluster's cert-manager wildcard — coupling the backup host's TLS to the cluster's cert-manager would undercut the isolation goal. Never run plaintext even on VLAN 25; DB credentials traverse it. Set `endpointCA` explicitly on clients to avoid the known CNPG/Barman SSL-verify failure against an internal S3 endpoint.

### Storage layout

A dedicated **plain Btrfs shared folder** on the Exos pool is the gateway root, bind-mounted into the container. `chown` it to the versitygw container UID:GID — apply the media-share lesson that empty perms leave files unusable across UIDs (see `docs/plans/20260513-arr-hardlink-rework.md`). Because objects are plain files, this share is fully DSM-introspectable.

### Secrets — NAS-scoped age recipient (the one deliberate divergence)

Reuse the SOPS+age model, with one hardening: **generate a fresh, NAS-scoped age keypair — do not reuse the cluster-wide recipient** (`age14wcg0t…`). Add one `creation_rule` to `.sops.yaml`:

```yaml
  - path_regex: synology/.*\.sops\.ya?ml
    encrypted_regex: "^(data|stringData)$"
    mac_only_encrypted: true
    age: "<NEW nas-scoped recipient>"
```

The host holding your last-resort backups must not carry a key that decrypts the whole repo, and a cluster-key or laptop loss must not also surrender the backup host. versitygw S3 keys and B2 credentials live in `synology/s3/secrets.sops.yaml` encrypted to that recipient only, independent of anything the cluster's secret store holds. doco-cd decrypts the SOPS-encrypted secrets natively at deploy time (it has built-in SOPS/age support), so plaintext is injected at deploy and never committed to git or left on disk. Keep the NAS private key root-only (`chmod 600`), sourced from 1Password at provisioning — matching the existing "age key lives in 1Password" posture; prefer `SOPS_AGE_KEY_CMD` pulling from 1Password at reconcile time if the NAS can reach it. Renovate's existing `ignorePaths: ["**/*.sops.*"]` already protects the encrypted file.

### Updates — zero new Renovate config

The native `docker-compose` manager (from `config:recommended`) reads `image: repo:tag` in `synology/s3/compose.yaml` the moment the file lands. Pin images as `repo:tag@sha256:…` so you get both tag-bump and digest PRs — and so the git commit itself is the deploy trigger (sidestepping the `up -d` "won't re-pull a mutable tag" gotcha). PRs inherit the repo's exact semantic-commit rules automatically (`feat(container)!:` major, `fix(container):` patch, `chore(container):` digest, `renovate/container` label). The existing custom regex manager already covers any version pinned in an env file or in doco-cd's own config via `# renovate:` annotations (including doco-cd's own image tag). Optionally add one `packageRule` giving `synology/` stacks `semanticCommitScope: synology` for changelog separation — cosmetic, not required.

### 3-2-1 with WORM on two+ independent tiers

| Tier | Mechanism | Role |
|---|---|---|
| **1 — application immutability** | versitygw S3 **Object Lock COMPLIANCE** on backup buckets, retention ≥ ransomware-detection window | Objects are WORM the moment they're written — immutable even to the gateway's own credentials |
| **2 — on-prem second copy** | DSM 7.2 **immutable (WORM) Btrfs snapshots** of the share (same mechanism already in production on the arr-suite media share, 7-day window) **+ Snapshot Replication to a distinct Btrfs target** | Local point-in-time recovery; snapshots capture xattrs (versitygw metadata fidelity). Per `docs/runbooks/longhorn-backup-restore.md`, same-volume snapshots are **not** a second copy — replication to a distinct target is what makes it one. |
| **3 — off-site immutable copy** | A Renovate-pinned `rclone` container in the same compose project syncs the **sealed** object tree to **Backblaze B2 with Object Lock (COMPLIANCE)** | The survivor of a total NAS loss or a DSM-side/admin compromise damaging both Btrfs tiers — the concrete defense against "DSM itself fails," solved at the data layer rather than with a hypervisor |

Sync already-*sealed* objects rather than locking a live restic/Kopia repo bucket (their index/lock files need mutation; a fully-locked bucket breaks them). CNPG/Barman's write-once-then-sealed pattern fits COMPLIANCE lock directly.

**Recovery posture:** because objects are plain files, a full-cluster-loss restore needs neither the cluster nor versitygw — read backups directly off the Btrfs share or off B2. This S3 store is independent of and complementary to the existing Velero/Longhorn NFS backup at `nfs://10.32.25.5:/volume1/backups-k8s-prod` (that protects the cluster; this is the clean, separate DB-backup-of-record target).

## Phased rollout

0. **Identity & secrets** — generate the NAS-scoped age keypair (private key → 1Password), add the `synology/.*` rule to `.sops.yaml`.
1. **Storage & network prep** — create the dedicated plain Btrfs share, `chown` to the container UID:GID; set up the VLAN-25 macvlan / `.6` assignment; add `s3-storage.home.kelch.io` to the architecture doc host table.
2. **TLS** — `acme.sh` DNS-01 cert for `s3-storage.home.kelch.io`; confirm renewal automation.
3. **Author the stack in git** — `synology/s3/compose.yaml` pinning `versity/versitygw:v1.5.0@sha256:…` (default xattr metadata mode, `restart: unless-stopped`, Btrfs share bind-mounted as root, TLS cert mounted) and `synology/s3/secrets.sops.yaml`.
4. **Deploy mechanism** — run **doco-cd** as a container in Container Manager, pointed at this repo's `synology/` tree (poll + webhook, mirroring the Flux webhook); it deploys `synology/s3/` on change and decrypts SOPS secrets natively. versitygw runs `restart: unless-stopped` so it self-heals independent of doco-cd. (Keep the hand-rolled `reconcile-stacks.sh` documented in reserve as the zero-dependency fallback.)
5. **Validate S3 + immutability before trusting it** — exercise the exact Object Lock + `ListObjectVersions` path your chosen backup tool uses against versitygw v1.5.x on a throwaway bucket; prefer Object Lock retention over version enumeration (versioning is flagged experimental, noncurrent versions live in a shadow `--versioning-dir`); confirm multipart + checksum headers; test the retention-reclaim path so a misconfigured lock can't pin unreclaimable disk.
6. **Wire one real producer end-to-end** — recommend CloudNativePG/Barman-cloud first; confirm a base backup + WAL land as plain files and a restore succeeds, with `endpointCA` set.
7. **Layer 3-2-1** — DSM immutable WORM snapshots + Snapshot Replication to a distinct target + the `rclone`→B2 Object Lock sidecar; verify xattr preservation on the off-site path (or run versitygw `--sidecar` so metadata is plain files too).
8. **Out-of-band success verification** — add a backup-success probe (healthcheck + DSM notification or dead-man's-switch ping); a backup target that fails silently is worse than none. Do a documented full-restore drill with versitygw **stopped** to prove the fault-isolation property.
9. **Generalize & document** — short `docs/runbooks/` entry for the `synology/` stack pattern; record Komodo as the named promotion path.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Single-NAS blast radius — a DSM bug, pool failure, or admin compromise damages both Btrfs tiers at once | The B2 COMPLIANCE Object Lock off-site tier is the independent survivor; treat its sync health as non-negotiable and verify out-of-band (the deploy loop succeeding ≠ the backup ran) |
| Silent failure of the push loop or off-site sync | Out-of-band success probe + periodic test-restore drill |
| versitygw versioning / `ListObjectVersions` is experimental | Exercise the exact lock+version path of the chosen tool against v1.5.x first; prefer Object Lock retention; SeaweedFS fallback only on a hard gap |
| Off-site fidelity depends on xattr preservation (versitygw stores object metadata in xattrs by default) | Use Btrfs send/receive or `rclone` (round-trips object bytes) for off-site; verify Hyper Backup preserves xattrs, or run `--sidecar` |
| COMPLIANCE Object Lock + immutable snapshots are irreversible to *you* | Set retention to a deliberate detection-window value; validate on a throwaway bucket; test the reclaim path; keep retention values in the committed compose for review |
| doco-cd is pre-1.0, and Compose has version-specific recreation regressions | doco-cd sits only in the deploy path — versitygw (`restart: unless-stopped`) + plain files on Btrfs + B2 mean a doco-cd failure never threatens the running service or the data; pin doco-cd + Compose + image digests, and keep the hand-rolled script as fallback |
| DSM major updates can disrupt Container Manager / wipe unsupported host artifacts | Keep everything in the committed Project + reconcile script; avoid hand-edited units; treat DSM upgrades as a change window with a verified restore afterward |

## Open questions

- **First backup producer & its exact S3 needs?** CNPG/Barman-cloud is the recommended first integration — confirm multipart/checksum-header and Object-Lock expectations against versitygw v1.5.x before designating it the backup-of-record.
- **Is a distinct on-prem Btrfs replication target available?** Snapshot Replication needs a separate target to be a real second copy. If none exists, B2 carries more weight.
- **Ransomware-detection window?** Sets COMPLIANCE + immutable-snapshot retention; pick a deliberate value before enabling locks.
- **Does the Flux webhook fan out cleanly to a second receiver on the NAS,** or is the Task Scheduler interval the only trigger? The interval works durably; the webhook is the "applies in seconds" nicety.
- **B2 vs R2 off-site?** B2 for true off-site WORM; R2 is zero-egress but has weaker Object Lock — revisit if egress economics dominate.
- **NAS age key: static root-only `keys.txt` vs `SOPS_AGE_KEY_CMD` from 1Password?** The latter is stronger but adds a 1Password dependency in the deploy path — confirm the NAS can reach 1Password at reconcile time.

## What changes in this repo

- New top-level `synology/` tree (sibling to `kubernetes/`, `talos/`): per-stack `compose.yaml` + `secrets.sops.yaml`. First inhabitant `synology/s3/`.
- **doco-cd** deployed as one container in Container Manager, watching `synology/` (its config lives in the tree). No bespoke deploy script in the primary path; `scripts/synology/reconcile-stacks.sh` kept only as the documented fallback.
- One new `.sops.yaml` `creation_rule` for `synology/.*` (NAS-scoped recipient).
- Architecture-doc host-table entry for `s3-storage.home.kelch.io → 10.32.25.6`.
- No new Renovate config (native docker-compose manager + existing custom managers suffice).

## Sources

Curated; the full research set (≈180 sources) lives in the workflow transcript.

**Substrate**
- [Container Manager Project — Synology KB](https://kb.synology.com/en-global/DSM/help/ContainerManager/docker_project?version=7)
- [Container Manager = Docker Engine 24.0.2 (blackvoid)](https://www.blackvoid.club/container-manager-24-0-2-1535/)
- [Container loses settings after image update — no reconcile loop (SynoForum)](https://www.synoforum.com/threads/container-manager-container-lose-settings-after-image-update.14319/)
- [VMM technical specs (free vs Pro)](https://www.synology.com/en-us/dsm/7.2/software_spec/vmm) · [VMM model list incl. DS1821+ (blackvoid)](https://www.blackvoid.club/synology-virtual-machine-manager-how-to-run-virtual-machines-on-synology-nas/)
- [DSM customized systemd / synosystemctl (Synology developer guide)](https://help.synology.com/developer-guide/resource_acquisition/systemd_user_unit.html)
- [k3s on Synology — missing systemd/snapshotter/cgroup modules](https://medium.com/@marco.mezzaro/k3s-on-synology-what-if-it-works-e980b4b09fcb) · [k3s no-skip-minor upgrades](https://docs.k3s.io/upgrades/manual)

**S3 engine**
- [versitygw POSIX backend — object key → file path](https://www.versity.com/versity-s3-gateway-posix-backend/) · [GitHub API repos/versity/versitygw (archived:false)](https://api.github.com/repos/versity/versitygw)
- [MinIO strips management console from Community Edition (Blocks & Files, Jun 2025)](https://blocksandfiles.com/2025/06/19/minio-removes-management-features-from-basic-community-edition-object-storage-code/) · [MinIO AIStor subscription tiers (min.io, Dec 2025)](https://www.min.io/blog/introducing-new-subscription-tiers-for-minio-aistor-free-enterprise-lite-and-enterprise) · [SynoCommunity MinIO frozen at last-FS-mode](https://synocommunity.com/package/minio)
- [Garage known issues — `replication_factor=1` LMDB corruption](https://garagehq.deuxfleurs.fr/documentation/reference-manual/known-issues/) · [Garage WORM/Object Lock blocked on versioning (#1127)](https://git.deuxfleurs.fr/Deuxfleurs/garage/issues/1127) · [Garage versioning WIP (#166)](https://git.deuxfleurs.fr/Deuxfleurs/garage/issues/166)

**Management**
- [doco-cd — Git-driven Compose CD (poll+webhook, native SOPS)](https://github.com/kimdre/doco-cd) · [doco-cd docs](https://doco.cd)
- [Komodo compose / git + webhook auto-redeploy](https://komo.do/docs/deploy/compose) · [Komodo Resource Syncs (TOML-in-git)](https://komo.do/docs/resources) · [Komodo SOPS pattern (discussion #934)](https://github.com/moghtech/komodo/discussions/934) · [Komodo on Synology setup friction (#616)](https://github.com/moghtech/komodo/issues/616)
- [`docker compose up` selective recreation (CLI ref)](https://docs.docker.com/reference/cli/docker/compose/up/) · [compose idempotency regression with `extends` (#10259)](https://github.com/docker/compose/issues/10259) · [compose-cd — systemd-timer git-pull pattern](https://github.com/grepler/compose-cd)

**Secrets, updates, backup path**
- [SOPS repo (general-purpose, MPL-2.0)](https://github.com/getsops/sops)
- [acme.sh Synology + Cloudflare DNS-01](https://cocallaw.com/posts/Automating-Lets-Encrypt-SSL-Certificates-on-Synology/)
- [CloudNativePG object stores for backups](https://cloudnative-pg.io/docs/1.28/appendixes/object_stores/) · [CNPG S3 SSL-verify failure vs internal S3 (#7946)](https://github.com/cloudnative-pg/cloudnative-pg/issues/7946) · [Barman 3.17 Object Lock support](https://docs.pgbarman.org/release/3.17.0/releases/index.html)
- [Backblaze B2 Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock)
