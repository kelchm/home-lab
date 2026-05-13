# *arr suite deployment plan

## Context

The home-lab cluster (Talos + Flux + Cilium BGP + Traefik + Longhorn) is fully bootstrapped with infra (cert-manager, three Traefik gateways, k8s-gateway DNS, Cloudflare tunnel, observability) but has zero media workloads. `docs/architecture.md` already anticipates this stack: it reserves Jellyfin's LB IP at `10.32.140.50` (architecture.md:198), names *arr content as the canonical NFS-bulk-storage tenant (architecture.md:318), and documents the storage-VLAN egress path that downloader/library traffic should ride. This plan delivers a GitOps-managed media stack — Prowlarr, Sonarr, Radarr, Lidarr, Readarr, Bazarr, qBittorrent (behind Gluetun), SABnzbd, Flaresolverr, Unpackerr, Recyclarr, Jellyfin, Jellyseerr — all using the established echo/grafana app pattern.

User-confirmed choices: qBittorrent + SABnzbd; Jellyfin + Jellyseerr; full *arr scope (TV/movies/music/books + helpers); single `/volume1/media` NFS share with hardlink-capable subdir layout; **per-app NAS identity** (real Synology users + a shared `media` group) instead of an `all_squash` blob, with `root_squash`-equivalent ("Map root to guest") for defense-in-depth; native *arr auth Disabled in favor of edge SSO. **Auth stack: Kanidm + traefik-oidc-auth, zero SPOF in the auth path.** Kanidm (Rust, CRDT-replicated multi-master, 3 replicas) is the user/password directory + OIDC OP + self-service web UI; OIDC-aware apps (Jellyseerr, Jellyfin via plugin) talk to it directly. The *arr/qBit/SAB management UIs that don't speak OIDC are gated by **`traefik-oidc-auth` running in-process inside the existing `traefik-admin` HelmRelease** as a yaegi plugin — no separate forwardAuth Deployment, no Valkey/Redis session store, no lldap, no Authelia. Cookie-only stateless sessions on the request path, replication-shard storage on the Kanidm side. Jellyfin keeps its native auth (client-compatibility); admin UIs ride `gateway-admin` (VLAN-10 only); Kanidm + Jellyseerr ride `gateway-services` so OIDC redirects and self-serve password changes work from streaming VLANs.

## Status (as of 2026-05-11)

What's deployed and where the implementation diverges from the prose below:

- **Phase 0 (Synology bootstrap)** — done. 9 service users + `media` group (gid 65537), `/volume1/media` directory tree with setgid 2775, NFS export to `10.32.25.11/.12/.13` with "Map root to guest", Btrfs snapshots (every 4h, retention 14d/8w/6mo, immutable 7d). Captured UIDs and export config: `docs/runbooks/arr-suite-bootstrap.md`.
- **Phase 1 (substrate)** — done. `csi-driver-nfs` at `kubernetes/apps/kube-system/csi-driver-nfs/`. Static `media-library` PV/PVC bound at `10.32.25.5:/volume1/media` (`kubernetes/apps/media/storage/`). **Kanidm + kaniop operator deployed in `kubernetes/apps/identity/`, not `kubernetes/apps/auth/` as written below** — references in subsequent prose to `auth/` namespace and `kubernetes/apps/auth/...` paths are plan-time intent; the hostname `auth.home.kelch.io` is unchanged. The `kanidm-oidc` Traefik Middleware lives in `media` namespace (`kubernetes/apps/media/middleware-kanidm-oidc.yaml`) — the cross-namespace ExtensionRef fallback flagged in the Phase 1 verification was needed.
- **Phase 2 (partial)** — Prowlarr and Flaresolverr deployed. End-to-end OIDC SSO verified via Prowlarr.
- **Next: SABnzbd** — first NFS-writing app; exercises Phase 0 layout + UIDs in anger. Smoke test of the NFS substrate (identity + hardlink + root-squash) runs immediately before, via `tools/media-nfs-smoke/smoketest.yaml`.
- Phases 3–8 pending per original prose.

The deployed shape uses the kaniop operator per `docs/plans/20260509-kaniop-migration.md`. Treat the hand-rolled Kanidm-StatefulSet design below as architectural rationale, not file-path truth.

## Approach

### Phase 0 — Prerequisites (one-time, blocks everything else)

1. **Synology prep — per-app identity, not `all_squash`** (manual, DSM UI + one SSH session)

   **a. Users + group** (SSH to NAS as admin, `sudo -i`):
   ```
   synogroup --add media
   # Only apps that actually mount the NFS share get a Synology user. API-only
   # apps (Prowlarr, Flaresolverr, Recyclarr, request UI) never touch the NAS.
   for u in qbittorrent sabnzbd sonarr radarr lidarr readarr bazarr unpackerr jellyfin; do
     synouser --add "$u" "$(openssl rand -hex 16)" "$u service" 0 "" 0
   done
   # CRITICAL: `synogroup --member` is a SET operation, not append — it replaces
   # the entire member list with the supplied users. Call once with all members,
   # NOT in a loop per user (that would leave only the last user in the group).
   synogroup --member media qbittorrent sabnzbd sonarr radarr lidarr readarr bazarr unpackerr jellyfin
   # Capture-then-pin: do NOT assume sequential UIDs. Existing users, deleted
   # accounts, package users, etc. can shift the allocation. DSM ships busybox
   # ash with no `getent`, so we read /etc/passwd and /etc/group directly.
   for u in qbittorrent sabnzbd sonarr radarr lidarr readarr bazarr unpackerr jellyfin; do
     grep "^${u}:" /etc/passwd | awk -F: '{printf "%-12s uid=%s gid=%s\n", $1, $3, $4}'
   done
   grep "^media:" /etc/group   # capture media GID
   ```
   Paste the actual output into `docs/runbooks/arr-suite-bootstrap.md`; that captured table is the source of truth pinned into per-app `runAsUser`. Disable shell + DSM access for each service user via DSM Control Panel → User.

   **b. Filesystem layout** — `/volume1/media` on a single volume; pre-create `tv/ movies/ music/ books/ audiobooks/ .downloads/manual/ .downloads/{torrents,usenet}/{.incomplete,tv,movies,music,books}`. The `.incomplete/` subdirs keep half-written downloads out of the category folders that *arr scanners watch; the leading dot also hides them from default listings. `.downloads/manual/` is a flat operator drop processed via each *arr's Manual Import UI.

   Then set ownership + setgid **scoped to our created subtree**, not the share as a whole — a freshly-created DSM share contains a `@eaDir` (file-indexing metadata, DSM-managed) and the share root itself comes out as `d---------+` (POSIX 000 plus a Synology ACL). Both need careful handling: don't chown `@eaDir` (DSM tooling assumes its ownership), and the share root needs a real POSIX mode so NFS clients can traverse:
   ```bash
   chown root:media /volume1/media
   chmod 2775 /volume1/media
   for top in tv movies music books audiobooks .downloads; do
     chown -R root:media "/volume1/media/$top"
     find "/volume1/media/$top" -type d -exec chmod 2775 {} +
   done
   ```
   The setgid bit causes new files/dirs to inherit `gid=media` regardless of the writer's primary group — required for cross-app hardlinks. Containers rely on `umask 002` (home-operations default).

   **c. NFS export** — share `/volume1/media`, NFSv4.1, allow the **node host storage NICs `10.32.25.11`, `.12`, `.13`** (architecture.md:144–146). **Not** the `10.32.25.128/28` pod /28 — that range is exclusively for Longhorn instance-manager Multus-attached pods (architecture.md:150). csi-driver-nfs mounts are kubelet-initiated, so the Synology sees the connection from the node's storage NIC, not from any pod IP. **Options**: async I/O on (throughput; durability via Btrfs snapshots + Longhorn-side recovery); "Allow connections from non-privileged ports" **OFF** (Linux NFS client defaults to `resvport`; flip ON only if a mount actually fails with "non-privileged port" in `dmesg`); "Allow users to access mounted subfolders" **OFF** (csi-driver-nfs mounts the share root; per-app scoping uses kernel-side `subPath` bind-mounts, not subdir NFS mounts). **Squash: "Map root to guest"** — preserves per-app UIDs end-to-end while neutering any UID-0 client (misconfigured pod, node-level mount session) to a no-group, mode-`other`-only identity. Make sure DSM's `guest` account has no ACL rights on the share.

   **d. Snapshots** — Btrfs snapshot policy on `/volume1/media` (hourly × 24, daily × 14, weekly × 8) outside the NFS-visible namespace. **Load-bearing** recovery mechanism for ransomware/oops-rm — call this out explicitly in the runbook; it is not optional.

   Hardlinks across subdirs are required for atomic *arr imports — single share, single filesystem, one shared `media` group makes it work.

2. **`csi-driver-nfs` HelmRelease** — new app at `kubernetes/apps/kube-system/csi-driver-nfs/` (chart: `kubernetes-csi/csi-driver-nfs`). Documented in `docs/architecture.md:318` but not yet deployed. Driver only — **no default StorageClass for `/volume1/media`**. The library is a static PV/PVC (next item) so the share root is mounted as-is, not a per-PVC `pvc-<uid>` subdir created by dynamic provisioning. A dynamic-provisioning `StorageClass` can be added later for ad-hoc NFS scratch volumes if a future app needs one. Add to `kubernetes/apps/kube-system/kustomization.yaml`.

3. **`media` namespace scaffolding** — `kubernetes/apps/media/{namespace.yaml, kustomization.yaml}` modeled on `kubernetes/apps/network/{namespace.yaml, kustomization.yaml}`. The top-level `kubernetes/flux/cluster/ks.yaml` walks `./kubernetes/apps` and picks up new directories automatically; no aggregator edit needed beyond creating `kubernetes/apps/media/kustomization.yaml` with `components: [../../components/sops]` and a `resources:` list of every `<app>/ks.yaml`.

4. **Shared `media-library` PV + PVC (static, not dynamic)** in the `media` namespace — `ReadWriteMany`, 50Ti. Static binding guarantees the mount is the share root (`/volume1/media`), not a per-PVC subdir. Files: `kubernetes/apps/media/storage/{media-library-pv.yaml, media-library-pvc.yaml}`.
   ```yaml
   # PV
   apiVersion: v1
   kind: PersistentVolume
   metadata: { name: media-library }
   spec:
     capacity: { storage: 50Ti }
     accessModes: [ReadWriteMany]
     persistentVolumeReclaimPolicy: Retain
     storageClassName: nfs-media-static    # empty class is fine; non-empty avoids default-class binding races
     mountOptions: [nfsvers=4.1, hard, noatime, nodiratime, rsize=1048576, wsize=1048576]
     csi:
       driver: nfs.csi.k8s.io
       volumeHandle: media-library          # must be cluster-unique
       volumeAttributes:
         server: nas-storage.home.kelch.io
         share: /volume1/media
   ---
   # PVC (in media namespace)
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata: { name: media-library, namespace: media }
   spec:
     accessModes: [ReadWriteMany]
     storageClassName: nfs-media-static
     volumeName: media-library
     resources: { requests: { storage: 50Ti } }
   ```
   All media pods that need library access mount it via `subPath`; read-only consumers add `readOnly: true` to the volumeMount.

5. **Kanidm (HA OIDC OP + LDAP + self-service UI) + traefik-oidc-auth (in-Traefik forwardAuth shim) — `kubernetes/apps/auth/`** — primary credential layer for every operator UI. UniFi VLAN-10 firewall scoping remains the perimeter; Kanidm provides the actual login. Native *arr auth gets **Disabled** in Phase 5 (edge auth + admin UIs on VLAN-10-only `gateway-admin` make the second prompt UX-pure-loss and breaks *arr mobile clients).

   > **2026-05-09 pivot:** The hand-rolled-StatefulSet design described below is being
   > replaced with the [pando85/kaniop](https://github.com/pando85/kaniop) operator.
   > See `docs/plans/20260509-kaniop-migration.md` for the operator-managed shape,
   > the cutover sequence, and the resulting changes to PR 5 (replication scaling)
   > and PR 6 (OIDC client secret discovery). The high-level architecture below
   > (Kanidm + traefik-oidc-auth, Gateway API HTTPRoute on `gateway-services`, no
   > net-new SPOF, etc.) is unchanged — only the deployment mechanism shifts from
   > hand-rolled YAML to operator CRs.

   **Why this shape.** The architecture doc names Authelia/Authentik forwardAuth as the eventual control (architecture.md:232), but on close inspection both options force three single-replica components into the auth path (forwardAuth process, user directory, session store) and any one of them becoming wedged blocks login to *every* admin UI simultaneously. Kanidm replaces all three with one HA-native component: it is *both* the user store *and* an OIDC OP, replicates active-active via CRDTs over mutual-TLS (no external Postgres, no Galera, no leader election, no Raft), and emits stateless JWTs that downstream RPs can validate without shared session state. Pairing Kanidm with `traefik-oidc-auth` — a yaegi-loaded Traefik plugin that does the OIDC-RP dance *inside the Traefik request path itself* — means the request-path auth component is also already HA, because the existing `traefik-admin` HelmRelease is already 2-replica (`helmrelease-admin.yaml:22`). Net: zero net-new SPOF. The only state is Kanidm's, and Kanidm is replicated.

   **Stack.**
   - New `auth` namespace: `kubernetes/apps/auth/{namespace.yaml, kustomization.yaml}`
   - `kubernetes/apps/auth/kanidm/{ks.yaml, app/{kustomization.yaml, ocirepository.yaml, helmrelease.yaml, secret.sops.yaml, httproute.yaml, certificate.yaml}}` — Kanidm as a 3-replica StatefulSet with a headless Service for stable per-replica hostnames (`kanidm-0.kanidm-headless.auth.svc...`), pod anti-affinity across the three control-plane nodes (`k8s-prod-1/2/3`), one Longhorn RWO PVC per replica at `/data` (2Gi, **`numberOfReplicas: 1`** because Kanidm replicates at the application layer; Longhorn 3× would be wasteful redundancy of a thing that's already redundant). Each replica owns its own Kanidm DB (an internal idlock+sled-style store) and replicates incrementally to the others over a mutual-TLS replication channel on port 8444. The replication CA is a cert-manager `Issuer` of kind `selfSigned` whose root cert is bound into each replica via a `Certificate` resource; this is internal-only crypto and never leaves the namespace. Public-facing TLS for `auth.home.kelch.io` (OIDC OP + admin UI) and the LDAPS endpoint comes from the cluster's existing cert-manager DNS01-Cloudflare flow. Resource budget: ~100Mi RAM × 3 ≈ 300Mi total; CPU 50m requests / 500m limits per replica is conservative but real.

     Public ports per replica:
     - **8443 / HTTPS** — admin web UI + OIDC OP (`/oauth2/openid/...` discovery, JWKS, /authorize, /token).
     - **3636 / LDAPS** — read-only LDAP for legacy clients (Jellyfin's SSO plugin uses LDAP, not OIDC).
     Replication-only port: **8444 / mTLS**, restricted by NetworkPolicy to peer Kanidm replicas.

     Kanidm uses **peppered Argon2id** for password storage (no OPAQUE; the server briefly sees plaintext on the registration POST — same threat model as Authelia's file backend). Argon2id at rest is the standard strong-password answer; the homelab tradeoff is that this account-creation path is HTTPS-only, single-tenant, and Kanidm doesn't log request bodies. Passkey/WebAuthn enrollment is supported natively but **deferred to a later phase** (per user direction); operator + housemates start password-only, layer in passkeys once the *arr stack is operational.

     Bootstrap: Kanidm chart writes the initial `idm_admin` random password to a Secret (`auth/kanidm-bootstrap-creds`) on first install. A one-shot Flux-managed Job `kanidm-seed` (deletes itself after success via `ttlSecondsAfterFinished`) consumes that Secret + a SOPS-encrypted seed config from `auth/kanidm/secret.sops.yaml` and `kanidm-cli`-creates the canonical groups (`arr-admins`, `media-users`), an operator account, and the two OIDC clients (`arr-suite` for the gated admin routes, `jellyseerr` for direct OIDC, plus `jellyfin` if the SSO plugin path is taken). Once the operator logs in to the web UI and rotates `idm_admin`'s password, the bootstrap Secret stays in git only as a placeholder — canonical password material is in Kanidm's replicated DB.

   - **`traefik-oidc-auth` plugin loaded into `traefik-admin`** — modify `kubernetes/apps/network/traefik/app/helmrelease-admin.yaml` to add the plugin to static config:
     ```yaml
     # additions to traefik-admin's values
     experimental:
       plugins:
         traefik-oidc-auth:
           moduleName: github.com/sevensolutions/traefik-oidc-auth
           version: v0.13.0          # pin; bump intentionally via Renovate
     ```
     The `traefik-services` HelmRelease is **not** modified — it has no plugin-protected routes (Jellyseerr/Jellyfin/Kanidm-UI all handle their own auth). The `traefik-public` HelmRelease likewise. Plugin lives only on the admin gateway.

     **Plugin failure mode**: if the pinned version stops loading (yaegi-incompatibility, plugin bug at startup, GitHub outage during pull), `traefik-admin` fails to start, taking down the whole admin gateway — not just auth. Mitigation: pin to a known-good version (never `latest`/`main`); test plugin version bumps in a throwaway HTTPRoute before merging the bump; document the rollback (revert the version pin in the HelmRelease, Flux reconciles, Traefik restarts) in the runbook. This is the price of in-process auth; for a homelab it's the right tradeoff vs. the operational complexity of a separate oauth2-proxy Deployment.

   - **OIDC Middleware that references the plugin** — `kubernetes/apps/network/traefik/app/middlewares/kanidm-oidc.yaml`:
     ```yaml
     apiVersion: traefik.io/v1alpha1
     kind: Middleware
     metadata: { name: kanidm-oidc, namespace: network }
     spec:
       plugin:
         traefik-oidc-auth:
           Provider:
             Url: https://auth.home.kelch.io
             ClientId: arr-suite
             ClientSecretFile: /run/secrets/traefik-oidc/client-secret
           Scopes: [openid, profile, email, groups]
           Authorization:
             AssertClaims:
               - Name: groups
                 AllOf: [arr-admins]      # only members of arr-admins reach *arr UIs
           Headers:
             MapClaims:
               - Claim: preferred_username
                 Header: Remote-User
               - Claim: email
                 Header: Remote-Email
               - Claim: groups
                 Header: Remote-Groups
           SessionCookie:
             Domain: home.kelch.io        # shared SSO across *.home.kelch.io
             Secure: true
             HttpOnly: true
             SameSite: lax
             MaxAge: 43200                # 12h
     ```
     The `arr-suite` OIDC client secret is mounted into the Traefik pod from a SOPS-encrypted Secret (`network/traefik-oidc-clientsecret.sops.yaml`) via the chart's `additionalVolumes` / `additionalVolumeMounts` values — the plugin reads it from the file path, never from an env var, so it doesn't leak into Traefik logs.

   - HTTPRoute on `gateway-services` for Kanidm at `auth.home.kelch.io` (OIDC OP + self-service UI). Households on streaming VLANs need to reach this endpoint to complete OIDC login redirects from Jellyseerr; VLAN-10 also has access to gateway-services, so admin flows work as well. Kanidm itself is *not* gated by the `kanidm-oidc` middleware — it's the auth source, gating it would create a chicken-and-egg.

   - **`kubernetes/apps/auth/policies/`** CiliumNetworkPolicies day-one (not deferred to Phase 8):
     - `kanidm.yaml` — ingress to `kanidm:8443` from gateway-services pods + cluster (Jellyseerr, Jellyfin); ingress to `kanidm:3636` from media namespace (Jellyfin SSO plugin); ingress to `kanidm:8444` only from peer Kanidm replicas (selector `app.kubernetes.io/name=kanidm`). Egress: cluster DNS, peer replicas, NTP. Nothing else.
     - `traefik-admin-oidc.yaml` — egress from `network/traefik-admin` to `auth/kanidm:8443` (plugin needs to fetch `/.well-known/openid-configuration` and JWKS at startup + on key-rotation cache misses).

   **Cross-namespace `ExtensionRef` smoke test before stamping 13 routes.** Cross-namespace Middleware refs in Gateway API can need a `ReferenceGrant`, and Traefik's ExtensionRef support depends on the IngressRoute/CRD provider being enabled — `traefik-admin` already has `kubernetesCRD.enabled: true` (`helmrelease-admin.yaml:33`), so this is unblocked. Phase 1 deploys the middleware + a single throwaway HTTPRoute in `media` referencing `network/kanidm-oidc`, walks the full redirect-login-redirect cycle in a browser, and verifies the session cookie sets on `.home.kelch.io` and that `Remote-User` / `Remote-Groups` reach the backend. If cross-namespace fails, fallback is to define the Middleware in `media` with a replicated client-secret Secret.

   **HA posture: 3-replica StatefulSet, no SPOF.** One Talos node down → 2 of 3 Kanidm replicas keep serving. Talos rolling upgrade → drains one replica at a time; the remaining 2 keep auth flowing. `traefik-admin` is already 2-replica with anti-affinity (per its HelmRelease), so the request path stays up across node drains as well. No SQLite, no Postgres, no shared volume — Kanidm's own replication is the durability story. Backups: Kanidm has a documented `kanidm server backup` CLI; a daily Flux-managed CronJob in `auth/kanidm/backup-cronjob.yaml` writes a snapshot to a small Longhorn PVC for restore-from-bricked-cluster recovery. Snapshot frequency is conservative because credential rotations are rare.

   Plugin-version-bump caveat aside, the only operationally-novel piece is Kanidm's replication bootstrap. The Helm chart handles the per-replica certs + replication peer config, but the first start of replica 0 is the "primary" until peer 1 and peer 2 have pulled an initial schema replication. Document this sequence in the runbook so a future operator who scales the StatefulSet to 0 and back to 3 doesn't get a confusing schema-drift error.

6. **Add `TIMEZONE: America/New_York` to `kubernetes/components/sops/cluster-secrets.sops.yaml`** so every HelmRelease can substitute `${TIMEZONE}` for `TZ`.

### Phase 1 — Storage + IdP + ingress foundation, smoke tested

Deploy nothing media-related yet. Prove the substrate:

- csi-driver-nfs deployed; static `media-library` PV/PVC bound; Synology shows the connection from `10.32.25.11/.12/.13` (see Verification → "NFS client source IP").
- A throwaway debug pod in `media` mounts `media-library` and confirms `/data` is the share root (`tv/`, `movies/`, …) and not a `pvc-<uid>` subdir; runs the hardlink, identity, and root-squash tests from the Verification section.
- **Kanidm 3-replica StatefulSet up in `auth` namespace**, all 3 pods Ready, replication healthy (`kanidm-cli system replication state` from a debug pod against each peer agrees on schema generation). HTTPRoute on `gateway-services` resolves at `auth.home.kelch.io`; web UI loads, `idm_admin` rotate-on-first-login completes, the seed Job created `arr-admins` group + operator user + `arr-suite` and `jellyseerr` OIDC clients. CiliumNetworkPolicies in `auth/policies/` are enforcing (validate by attempting an unauthorized cross-namespace connect to `kanidm:8444`).
- **`traefik-oidc-auth` plugin loaded into `traefik-admin`** without errors (`kubectl -n network logs deploy/traefik-admin | grep -i 'plugin'` shows successful registration; no yaegi errors). Plugin pinned to `v0.13.0` (or whatever current GA is at deploy time).
- `kanidm-oidc` Middleware deployed; one throwaway HTTPRoute in `media` references `network/kanidm-oidc` cross-namespace and exercises the full OIDC dance: unauthenticated request → 302 to `auth.home.kelch.io/ui/oauth2/...` → submit creds → consent (or skipped on returning visit) → 302 back with `traefik-oidc-auth` session cookie on `.home.kelch.io` → backend reached. Backend sees `Remote-User` and `Remote-Groups: arr-admins` headers. Tear the throwaway route down once verified.
- **HA smoke test**: kill any one Kanidm pod (`kubectl -n auth delete pod kanidm-1`); login still succeeds against the remaining 2 replicas during the ~30s before the StatefulSet brings the killed pod back. Repeat with `traefik-admin` (one of its 2 replicas): login still succeeds.

Only once all four substrate checks pass do app deploys begin.

### Phase 2 — Indexer + low-risk download client

7. **Prowlarr** — `kubernetes/apps/media/prowlarr/`. **No media mount** (Prowlarr never touches the library). Config PVC only. First "real" app — validates the per-app skeleton end-to-end (Flux Kustomization, OCIRepository, HelmRelease, Longhorn config PVC, gateway-admin route + middleware, native auth) on a workload that can't damage anything.
8. **Flaresolverr** — `kubernetes/apps/media/flaresolverr/`. ClusterIP only, no PVC, no media mount, no HTTPRoute. Prowlarr targets `http://flaresolverr.media.svc.cluster.local:8191`.
9. **SABnzbd** — `kubernetes/apps/media/sabnzbd/`. No VPN. Mounts `media-library` at `/data` with `subPath: .downloads/usenet` (RW). First library writer — validates per-app NAS UID + setgid + hardlinks against real NFS.

### Phase 3 — VPN downloader

10. **qBittorrent + Gluetun (one Pod, two containers)** — `kubernetes/apps/media/qbittorrent/`
   - bjw-s app-template with **Gluetun as a native sidecar init container** (Kubernetes 1.28+: `initContainers[].restartPolicy: Always`). The bjw-s app-template exposes this via `controllers.qbittorrent.initContainers.gluetun`. Native sidecars start before regular containers and remain running for the pod's lifetime, so the qB app container only starts after Gluetun is up. The two containers share the pod's network namespace; Gluetun's `iptables` killswitch + tun device on that shared namespace force qB egress through the tunnel and drop any non-VPN egress. (qB still has network access — the distinction is *what's reachable* from the shared namespace, not whether qB has interfaces.)
   - Pod sysctl `net.ipv4.conf.all.src_valid_mark=1` for WireGuard.
   - Gluetun container: `securityContext.capabilities.add: [NET_ADMIN]`, `volumeMounts: /dev/net/tun` from a `hostPath` (Talos exposes `/dev/net/tun`; verify with `kubectl debug node/...`).
   - Gluetun env: configure `FIREWALL_OUTBOUND_SUBNETS` to allow the pod CIDR + admin VLAN as local networks so the qB Web UI is reachable from cluster + LAN while torrent egress stays VPN-only.
   - Web UI Service + HTTPRoute (`gateway-admin`, with `gateway-admin-auth` middleware) target the qB container port. Inbound Web UI hits the pod via the normal Service path; Gluetun's firewall must allow that input.
   - Default `admin/adminadmin` is unacceptable: ship `WEBUI_PASSWORD_PBKDF2` env from `secret.sops.yaml` so the password is set at startup (no manual post-deploy step). Verify the home-operations qBittorrent image actually honors `WEBUI_PASSWORD_PBKDF2` at startup — fall back to `WEBUI_PASSWORD` (plaintext, image-side hashed) if it doesn't.
   - Hardening: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]` from day one. **`readOnlyRootFilesystem: true` is opt-in after smoke** — many media images write outside `/config`/`/data` (e.g. `/tmp`, runtime caches); enable once you know what `emptyDir` mounts you need. Gluetun keeps `NET_ADMIN` only.
   - `secret.sops.yaml` holds VPN creds (`WIREGUARD_PRIVATE_KEY`, `WIREGUARD_ADDRESSES`, `SERVER_COUNTRIES`, `VPN_SERVICE_PROVIDER`) + `WEBUI_PASSWORD_PBKDF2`. Provider TBD (see Open Questions).

### Phase 4 — Core *arr apps

> **2026-05-13 pivot:** Per-app `subPath` mounts for Sonarr/Radarr/Lidarr/Bazarr
> are being replaced with a single share-root `globalMounts: [{ path: /data }]`
> mount, and the "shared `media` group as writer identity" isolation model is
> being replaced with per-folder NFSv4 ACLs on the NAS. See
> `docs/plans/20260513-arr-hardlink-rework.md` for the rework's full
> rationale (separate-NFS-mounts breaks `link(2)` across category dirs),
> the manifest deltas, and the operator execution sequence. The high-level
> shape below (Flux Kustomization, OCIRepository, HelmRelease, Longhorn
> config PVC, gateway-admin route + middleware) is unchanged.

11. **Sonarr** — subPaths `tv` (RW) + `.downloads` (RW for cleanup post-import).
12. **Radarr** — subPaths `movies` (RW) + `.downloads` (RW).
13. **Lidarr** — subPaths `music` (RW) + `.downloads` (RW).
14. **Readarr** — subPaths `books` (RW) + `audiobooks` (RW) + `.downloads` (RW). **Defer to last in this phase** — fussier metadata than the other three; bring it up after Sonarr/Radarr/Lidarr are stable.

All four share the per-app pattern below; deltas are image, container port, library subPaths, and `runAsUser`.

### Phase 5 — Helpers

> **2026-05-13 pivot:** Bazarr is rolled in with the Phase 4 mount-shape
> rework above (single share-root mount, NFSv4 ACLs). See
> `docs/plans/20260513-arr-hardlink-rework.md`. Unpackerr and Recyclarr
> are unchanged: unpackerr keeps `subPath: .downloads` (no cross-category
> hardlinks needed); Recyclarr has no media mount.

15. **Bazarr** — subPaths `tv`, `movies` (RW for sidecar subtitle files); no `.downloads` access.
16. **Unpackerr** — `kubernetes/apps/media/unpackerr/`. No web UI; mounts `media-library` with subPath `.downloads` (RW — extracts archives in-place inside the `.downloads` tree). Never writes to the library; *arr does the library import. Needs *arr API keys + URLs from `secret.sops.yaml`.
17. **Recyclarr** — `kubernetes/apps/media/recyclarr/`. bjw-s `controllers.recyclarr.type: cronjob`, daily. **No media mount** — only talks to *arr APIs. TRaSH-guide YAML in a `ConfigMap`; API keys in `secret.sops.yaml`.

### Phase 6 — Media server

18. **Jellyfin** — `kubernetes/apps/media/jellyfin/`
    - HTTPRoute on `gateway-services` (no auth middleware; Jellyfin's native auth is mature). Dedicated LB IP `10.32.140.50` reserved in architecture.md:198 can be added later if direct-IP clients (Apple TV, etc.) need it — start with HTTPRoute only, and if you add the LB later, keep the same hostname + cert behind it so Jellyseerr/clients don't see two inconsistent base URLs.
    - `jellyfin-config` PVC on `longhorn` (20Gi); `media-library` mounted **`readOnly: true`** at `/data` (defense-in-depth — Jellyfin should never write into the library).
    - VAAPI hardware accel: hostPath `/dev/dri` + supplementalGroups for the host's `video` + `render` GIDs. **GID discovery sub-step**: `kubectl debug node/k8s-prod-1 -it --image=busybox -- ls -ln /dev/dri` to capture the actual GIDs on Talos; pin both into `securityContext.supplementalGroups: [<media-gid>, <video-gid>, <render-gid>]`. The 705 G4 Vega 11 iGPU is the transcode target.
    - Get libraries, VAAPI, and reverse-proxy headers (`Network → Known Proxies` set to gateway-services pod CIDR for correct client IPs) all working before Phase 7.

### Phase 7 — Request UI

19. **Jellyseerr** — `kubernetes/apps/media/jellyseerr/`. HTTPRoute on `gateway-services`; auth via **Kanidm OIDC** (Kanidm is the OP, Jellyseerr is the RP). The `kanidm-oidc` Traefik middleware is **not** attached to this route — Jellyseerr handles the OIDC dance itself, putting the plugin in front would double-redirect. **No media mount** — only talks to *arr + Jellyfin APIs. Depends on a healthy Jellyfin + Sonarr + Radarr + Kanidm.

### Phase 8 — Lockdown

20. **CiliumNetworkPolicies** in `kubernetes/apps/media/policies/`. Default-deny intra-namespace; explicit allows for the real graph:
    - Prowlarr → Flaresolverr; Prowlarr → Sonarr/Radarr/Lidarr/Readarr
    - *arr → qBittorrent/SABnzbd
    - Bazarr → Sonarr/Radarr
    - Jellyseerr → Jellyfin/Sonarr/Radarr
    - Unpackerr → *arr; Recyclarr → *arr
    - gateway namespaces → media UIs
    - all media pods → `nas-storage.home.kelch.io:2049` (only via node — but worth scoping egress anyway)
21. Revisit `readOnlyRootFilesystem: true` per app now that you know what `emptyDir` mounts each image needs.
22. First backup/restore drill against the snapshot policy from Phase 0.

### Per-app NAS identity (UID table)

**UIDs are not pre-known.** Capture the actual values from Phase 0 step 1a, paste into the runbook, then pin into per-app HelmReleases. Do not assume sequential 1026+ allocation — existing users, deleted accounts, or DSM package users can shift the order. The table below shows the *shape* (which app gets which library access); the UID column is filled in by the operator after capture.

| App         | UID (fill in) | Library access                          | Notes                                    |
|-------------|---------------|------------------------------------------|------------------------------------------|
| qbittorrent |               | .downloads RW                             | Highest blast radius — extra hardening   |
| sabnzbd     |               | .downloads/usenet RW                      |                                          |
| sonarr      |               | tv RW + .downloads RW                     |                                          |
| radarr      |               | movies RW + .downloads RW                 |                                          |
| lidarr      |               | music RW + .downloads RW                  |                                          |
| readarr     |               | books/audiobooks RW + .downloads RW       |                                          |
| bazarr      |               | tv/movies RW                             | No `.downloads`                          |
| unpackerr   |               | .downloads RW                             | Extracts in-place; *arr handles import   |
| jellyfin    |               | library RO                               | + supplementalGroups for /dev/dri        |

Capture the `media` GID the same way and reuse as the shared `supplementalGroup` (and as `runAsGroup` — see pattern below) across all writers and Jellyfin. Apps without a Synology user (no NFS access at all): Prowlarr, Flaresolverr, Recyclarr, and the request UI (Jellyseerr/Seerr) — all API-only, reach *arr/Jellyfin over the cluster network.

### Per-app pattern (every app above)

Mirror `kubernetes/apps/default/echo/` exactly:

```
kubernetes/apps/media/<app>/
├── ks.yaml                  # Flux Kustomization, targetNamespace: media,
│                            # postBuild.substituteFrom cluster-secrets
└── app/
    ├── kustomization.yaml   # lists helmrelease + ocirepository + (optional) secret.sops.yaml + configmap.yaml
    ├── ocirepository.yaml   # bjw-s app-template (copy verbatim from echo: tag 4.6.2)
    ├── helmrelease.yaml
    └── secret.sops.yaml     # only where the app needs API keys / VPN creds
```

HelmRelease skeleton (deltas per app: image, env, persistence subPaths/readOnly, route hostname, port, runAsUser):

```yaml
spec:
  chartRef: { kind: OCIRepository, name: <app> }
  interval: 1h
  values:
    defaultPodOptions:
      securityContext:
        runAsUser:  <app-uid>             # see UID table
        runAsGroup: <media-gid>           # process primary group = shared media group;
                                          # makes new file group ownership independent of setgid
        supplementalGroups: [<media-gid>] # belt + suspenders (omit for prowlarr/flaresolverr/recyclarr/jellyseerr|seerr)
        fsGroupChangePolicy: OnRootMismatch
        # No pod-level fsGroup — csi-driver-nfs does not advertise fsGroup ownership-change
        # support, and we don't want a recursive chown racing NAS-side ownership.
        # Per-volume fsGroup on the config PVC (Longhorn block FS) is fine; see persistence.config below.
    controllers:
      <app>:
        containers:
          app:
            image: { repository: ghcr.io/home-operations/<app>, tag: <pinned> }
            env: { TZ: ${TIMEZONE} }
            probes: { liveness: {...}, readiness: {...} }
            resources: { requests: {cpu: 50m, memory: 256Mi}, limits: {memory: 1Gi} }
            securityContext:
              allowPrivilegeEscalation: false
              capabilities: { drop: [ALL] }
              # readOnlyRootFilesystem: true  # enable per-app after verifying image tolerates it
    service:
      app: { ports: { http: { port: <port> } } }
    persistence:
      config:
        type: persistentVolumeClaim
        storageClass: longhorn
        size: 5Gi
        accessMode: ReadWriteOnce
        fsGroup: <app-uid>                 # Longhorn block FS supports recursive chown
        advancedMounts: { <app>: { app: [{ path: /config }] } }
      media:                               # omit entirely for prowlarr / recyclarr / jellyseerr / flaresolverr
        type: persistentVolumeClaim
        existingClaim: media-library
        advancedMounts:
          <app>:
            app:
              - path: /data/<subdir>
                subPath: <subdir>
                readOnly: <true|false>     # see UID table; jellyfin = true
    route:
      app:
        hostnames: ["<app>.home.kelch.io"]
        parentRefs: [{ name: gateway-admin, namespace: network, sectionName: https }]
        rules:
          - backendRefs: [{ identifier: app, port: <port> }]
            filters:
              - type: ExtensionRef
                extensionRef:
                  group: traefik.io
                  kind: Middleware
                  name: kanidm-oidc            # SSO via Kanidm + traefik-oidc-auth plugin
                  namespace: network
```

`ghcr.io/home-operations/<image>` is the actively maintained fork (formerly onedr0p) — Renovate-friendly, distroless-leaning. Default user varies per image; verify with `docker inspect ghcr.io/home-operations/<app> | jq '.[0].Config.User'` and confirm `runAsUser` overrides cleanly without breaking the image's expectations (e.g., `/config` ownership at startup) before pinning.

### Inter-app wiring (interleaved with phases above)

Document in a new runbook `docs/runbooks/arr-suite-bootstrap.md`. Mostly UI configuration:

1. Prowlarr: add indexers; set Flaresolverr URL; configure Apps → Sonarr/Radarr/Lidarr/Readarr (auto-syncs indexers).
2. *arr apps → Settings → Download Clients: add qBittorrent (`http://qbittorrent.media.svc.cluster.local:8080`) + SABnzbd (`http://sabnzbd.media.svc.cluster.local:8080`); set categories `tv movies music books`.
3. *arr apps → Settings → Media Management: set root folders under `/data/{tv,movies,music,books}`; enable hardlinks; verify "Use Hardlinks instead of Copy" works (test via the Phase 1 hardlink check). Configure qBittorrent + SABnzbd to write incomplete files into `.downloads/{torrents,usenet}/.incomplete/` and only move into the category dirs on completion.
4. *arr apps → Settings → General → Security: set **Authentication Required = Disabled** (or, where the *arr exposes it, `Authentication Method = External` with `Authentication Required = DisabledForLocalAddresses`). The `kanidm-oidc` Traefik middleware is the operator-UI gate; native auth would mean a second prompt with no shared session. **API-key auth stays enabled** — Sonarr/Radarr/etc. APIs are reached server-to-server via cluster Service (Recyclarr, Unpackerr, Bazarr, Jellyseerr), bypassing Traefik and therefore the OIDC plugin, so the API key is the only credential on those paths. Capture each app's API key into its `secret.sops.yaml` (step 9) so cross-app config stays declarative.
5. Bazarr: add Sonarr + Radarr endpoints.
6. Recyclarr: verify CronJob sync writes profiles into each *arr.
7. Jellyfin: add libraries from `/data/{tv,movies,music,books,audiobooks}`; enable VAAPI; set Network → Known Proxies to the gateway-services pod CIDR for correct client IPs.
8. Jellyseerr: connect Jellyfin + Sonarr + Radarr; configure **Kanidm OIDC** as the sign-in provider (issuer `https://auth.home.kelch.io/oauth2/openid/jellyseerr`, client_id `jellyseerr`, client_secret from `jellyseerr/secret.sops.yaml`, redirect URI `https://jellyseerr.home.kelch.io/api/v1/auth/oidc-callback`). Jellyseerr is OIDC-aware natively, so no Traefik plugin sits in front — it handles the OAuth dance itself. Family/housemate accounts must exist **in Kanidm** to sign in; they create + manage their own passwords (and, later, passkeys) at `auth.home.kelch.io`. Jellyfin keeps its own separate user list for streaming-client compatibility, with optional Jellyfin SSO plugin pointed at Kanidm's LDAPS for federated login if/when that's wanted.

## Critical files to create / modify

**New** (each follows the per-app pattern above):
- `kubernetes/apps/kube-system/csi-driver-nfs/{ks.yaml, app/{kustomization.yaml, helmrelease.yaml, helmrepository.yaml}}` (no StorageClass for media; driver only)
- `kubernetes/apps/auth/{namespace.yaml, kustomization.yaml}` plus:
  - `kanidm/{ks.yaml, app/{kustomization.yaml, ocirepository.yaml, helmrelease.yaml, secret.sops.yaml, httproute.yaml, certificate.yaml, seed-job.yaml, backup-cronjob.yaml}}`
  - `policies/{kanidm.yaml, traefik-admin-oidc.yaml}` — CiliumNetworkPolicies day-one
- `kubernetes/apps/media/{namespace.yaml, kustomization.yaml, ks.yaml}` plus `storage/{media-library-pv.yaml, media-library-pvc.yaml}`
- `kubernetes/apps/media/policies/` (Phase 8 CiliumNetworkPolicies)
- `kubernetes/apps/media/{prowlarr,qbittorrent,sabnzbd,sonarr,radarr,lidarr,readarr,bazarr,flaresolverr,unpackerr,recyclarr,jellyfin,jellyseerr}/` — 13 apps × the per-app skeleton
- `kubernetes/apps/network/traefik/app/middlewares/kanidm-oidc.yaml`
- `kubernetes/apps/network/traefik/app/traefik-oidc-clientsecret.sops.yaml` (Secret holding the `arr-suite` OIDC client secret, mounted into traefik-admin pods)
- `docs/runbooks/arr-suite-bootstrap.md`

**Modified**:
- `kubernetes/apps/kube-system/kustomization.yaml` — add `./csi-driver-nfs/ks.yaml`
- `kubernetes/apps/network/traefik/app/kustomization.yaml` — add `./middlewares/`, add the OIDC client-secret Secret
- `kubernetes/apps/network/traefik/app/helmrelease-admin.yaml` — add `experimental.plugins.traefik-oidc-auth` block; add `additionalVolumes`/`additionalVolumeMounts` to mount the `arr-suite` client-secret file at `/run/secrets/traefik-oidc/client-secret`
- `kubernetes/components/sops/cluster-secrets.sops.yaml` — add `TIMEZONE`
- (If/when Renovate is adopted in this repo) `renovate.json5` — group `ghcr.io/home-operations/*` so 13 image bumps land as a single PR. No-op today; flagged for the operator.

**Reference (copy from)**:
- `kubernetes/apps/default/echo/{ks.yaml, app/*}` — canonical bjw-s app-template skeleton with embedded `route` block
- `kubernetes/apps/observability/grafana/app/httproute.yaml` — gateway-admin HTTPRoute example
- `kubernetes/apps/network/traefik/app/gateways.yaml` — gateway names + sectionName references

## Verification (end-to-end)

**Phase 1 substrate (run before any real app deploys):**
- **NFS client source IP**: while a debug pod has `media-library` mounted, on the Synology run `netstat -tn | grep ':2049'` (or DSM → Resource Monitor → Connection). Source IPs must be `10.32.25.11/.12/.13` (node host NICs), **not** any pod IP. Document in the runbook.
- **PVC mounts share root, not a subdir**: `kubectl -n media exec <debug> -- sh -c 'ls -la /data && find /data -maxdepth 2 -type d | sort | head -50'` shows `/data/tv`, `/data/movies`, `/data/.downloads/torrents/incomplete`, etc. — **not** `/data/pvc-...`.
- **Hardlink across real category paths** (matches actual downloader layout):
  ```
  kubectl -n media exec <debug> -- sh -c '
    touch /data/.downloads/torrents/tv/hardlink-test &&
    ln /data/.downloads/torrents/tv/hardlink-test /data/tv/hardlink-test &&
    stat -c "%h %i %n" /data/.downloads/torrents/tv/hardlink-test /data/tv/hardlink-test
  '
  ```
  Same inode, link count = 2.
- **Root-squash from a UID-0 pod with the PVC mounted** (the realistic failure mode, not just a node mount):
  ```
  kubectl -n media run nfs-root-test --rm -it --image=busybox \
    --overrides='{"spec":{"securityContext":{"runAsUser":0,"runAsGroup":0},
      "containers":[{"name":"x","image":"busybox","stdin":true,"tty":true,
        "command":["sh"],"volumeMounts":[{"name":"m","mountPath":"/mnt"}]}],
      "volumes":[{"name":"m","persistentVolumeClaim":{"claimName":"media-library"}}]}}' \
    -- sh
  # inside: touch /mnt/root-test  → must fail
  ```
  Also still run the node-level mount test as a second case. Confirm DSM's `guest` account has zero ACL rights on the share.
- **Cross-namespace Middleware ExtensionRef**: throwaway HTTPRoute in `media` referencing `network/kanidm-oidc` returns a 302 to `auth.home.kelch.io/ui/oauth2/...` (not the backend) for an unauthenticated request.

**Per-app (run for every writer after each app deploys):**
- **Identity check**: `kubectl -n media exec deploy/<app> -- id` returns the pinned UID + `media` as primary group; `kubectl -n media exec deploy/<app> -- sh -c 'touch /data/<subdir>/<app>-write-test && ls -ln /data/<subdir>/<app>-write-test'` shows owner `<app-uid>:<media-gid>`.
- **Read-only enforcement (Jellyfin)**: `kubectl -n media exec deploy/jellyfin -- touch /data/test` fails with `Read-only file system` (mount-level), not EACCES (perm-level).

**qBittorrent + Gluetun specifically:**
- **VPN exit IP from both containers** — Gluetun proves it has VPN, qB proves it's actually using it:
  ```
  kubectl -n media exec deploy/qbittorrent -c gluetun -- wget -qO- https://am.i.mullvad.net/json
  kubectl -n media exec deploy/qbittorrent -c app     -- wget -qO- https://am.i.mullvad.net/json
  ```
  Both must show the VPN exit IP, not your ISP's.
- **Killswitch**: stop the Gluetun container → qB loses egress entirely (no fallback to host network).
- **Web UI reachable while VPN is up** (catches over-restrictive Gluetun firewall): `curl -I https://qbittorrent.home.kelch.io` returns 401 (basic-auth challenge from Traefik), then qB login after auth.

**Top-level:**
- `flux get ks -n flux-system` shows all 13 media + csi-driver-nfs Kustomizations `Ready=True`.
- `kubectl -n media get pvc` shows `media-library` Bound to the static `media-library` PV; all per-app `*-config` PVCs Bound on `longhorn`.
- DNS: `dig prowlarr.home.kelch.io @10.32.130.2` → `10.32.130.1`; browser to `https://prowlarr.home.kelch.io` redirects to `auth.home.kelch.io/ui/oauth2/...`, accepts operator credentials, redirects back to Prowlarr — and Prowlarr loads straight into its dashboard with **no second login** (native auth Disabled). Subsequent loads of `sonarr.home.kelch.io` in the same session are instant (shared `.home.kelch.io` cookie set by `traefik-oidc-auth`).
- Jellyseerr OIDC: `https://jellyseerr.home.kelch.io` → "Sign in with Kanidm" → Kanidm login → redirect back; Jellyseerr profile shows the Kanidm username and email. Logout from Jellyseerr does NOT log out other apps (RP-initiated front-channel logout across multiple RPs is out of scope for now).
- **Kanidm HA proof**: `kubectl -n auth delete pod kanidm-1` while a browser session is mid-OIDC-flow; redirect completes against the surviving 2 replicas (Service round-robin picks a healthy pod), `kanidm-1` rejoins replication within ~30s and `kanidm-cli system replication state` shows it caught up.
- End-to-end: request a public-domain title via Jellyseerr → Radarr grabs → qB or SAB downloads to `incomplete/` then category dir → Unpackerr extracts → file lands in `/data/movies` → Jellyfin scans and shows it.

## Open questions (resolve before first deploy)

- **VPN provider** for Gluetun (Mullvad / ProtonVPN / Airvpn / Nord). `qbittorrent/app/secret.sops.yaml` ships with placeholder fields + a comment; populate before pushing. Only blocks qBittorrent (Phase 3).
- **Talos `/dev/dri` GIDs** for Jellyfin VAAPI — capture via `kubectl debug node/k8s-prod-1 -it --image=busybox -- ls -ln /dev/dri` once cluster is reachable; pin into Jellyfin's `supplementalGroups`. Blocks Jellyfin transcode but not initial deploy.
- **`/dev/net/tun` exposure on Talos** — verify it's accessible via `kubectl debug node/...`; if not, identify the Talos `machine.kubelet.extraMounts` (or equivalent) needed before Phase 3.
- **Native sidecar init container support** — bjw-s app-template version pinned in `echo` (4.6.2) is recent enough; verify `controllers.<name>.initContainers.<sidecar>.restartPolicy: Always` renders correctly into the generated pod spec before Phase 3.
- **home-operations image default users** — most run as a fixed app UID (e.g., `apps:apps` 568:568), some as `nobody`. Verify per-image with `docker inspect ... | jq '.[0].Config.User'` so `runAsUser` overrides cleanly without breaking image expectations (e.g., `/config` ownership at startup, hardcoded paths owned by the original UID).
- **qBittorrent `WEBUI_PASSWORD_PBKDF2` honored?** — verify the home-operations qBittorrent image actually consumes that env at startup; fall back to `WEBUI_PASSWORD` if not. Either way, default `admin/adminadmin` must be unset before the pod is exposed.
- **Kanidm bootstrap sequencing** — does the chosen Kanidm Helm chart write the initial `idm_admin` random password to a Secret automatically, or does the operator need to scrape it from pod logs? Verify before merging the seed Job; if it's logs-only, the Job needs an init step that reads `kubectl logs` (a privileged read in `auth`). Not blocking, but determines whether the seed flow is fully declarative.
- **Jellyfin SSO plugin via Kanidm LDAPS** — optional. Today plan keeps Jellyfin on its native auth for client compatibility. If/when the operator wants federated Jellyfin login (so housemates use one set of credentials for Jellyseerr + Jellyfin), enroll the plugin and point it at `ldaps://kanidm.auth.svc.cluster.local:3636` with a service-bind user. Not blocking.
- **Bringing other admin UIs onto Kanidm** — Grafana, Longhorn UI, etc. are out of scope for this plan but the `kanidm-oidc` Middleware exists once Phase 1 is done; their HTTPRoutes can adopt it incrementally.
- **Passkey/WebAuthn rollout** — deferred per user direction. Once the *arr stack is operational, enroll passkeys for the operator account first, then offer to housemates. Kanidm supports WebAuthn natively; no architecture change needed.
- **Plugin version bump policy** — `traefik-oidc-auth` is community-maintained. Track upstream releases via Renovate (group with `traefik` so Traefik chart bumps and plugin bumps land together for testing); never auto-merge plugin bumps without a manual smoke against a throwaway HTTPRoute. Not blocking initial deploy.
