# Bambuddy bootstrap and recovery

Bambuddy runs in `printing` with a single-instance CloudNativePG database on a
5 GiB Longhorn PVC. Live archives use a separate 50 GiB Longhorn PVC. It is
reachable only through `gateway-admin` at
`https://bambuddy.home.kelch.io`. Automatic discovery, host networking,
Virtual Printer, Proxy mode, external archive roots, and cloud integrations are
out of scope for the initial deployment.

## Before making the PR ready

1. Record the printer model and confirm it is on the IoT VLAN (`10.32.90.0/24`).
   Record the actual UniFi rule state for Talos compute-node traffic to that
   zone. Pod traffic is currently masqueraded to node addresses, and the
   current default-allow posture makes this a reachability check rather than
   proof of an enforced zone boundary.
2. Decide whether LAN Only/Developer Mode is acceptable for that model.
3. From a temporary pod subject to the intended policy, test the model's
   complete workflow: connect over MQTT/TLS `8883`, upload and start a real
   test job, and exercise FTPS `990`, RTSPS `322`, A2L camera/file transfer
   `6000`, and A1/P1S ports `2024-2026` as applicable. Watch Hubble for denied
   secondary connections, especially FTPS passive data ports.
4. Treat the configured port union as provisional until the real printer model
   passes that workflow. Do not respond to a passive-port failure by allowing
   a broad ephemeral range to the entire VLAN without separate review.
5. Accept and record the interim residual risk: every host in `10.32.90.0/24`
   listening on an allowed TCP port is reachable from Bambuddy. Printer
   addresses remain configured only in Bambuddy's UI; the UI inventory is not
   a network enforcement boundary.
6. Treat 50 GiB as the initial allocation, not a permanent forecast. Upstream
   estimates 500 prints with some timelapses at 1-5 GB and 1,000+ prints with
   full timelapses at 10+ GB. Record actual growth after 30 and 60 days and
   expand before the volume reaches 80% utilization. Move only bulk archives
   to NFS in a separately reviewed change.

## OIDC prerequisite and first login

Do not enable OIDC yet. `auth.home.kelch.io` currently resolves inside the
cluster to the private `10.32.140.1` LoadBalancer address, and Bambuddy rejects
private, loopback, and link-local OIDC issuer addresses as SSRF protection.
Before making this PR ready, provide a separately reviewed HTTPS Kanidm issuer
whose DNS resolves to a non-private address from the Bambuddy pod and whose
discovery document and token `iss` claim use that exact issuer. An alias in
front of the current issuer is not sufficient if those values still name
`auth.home.kelch.io`.

Until that prerequisite exists, keep the local administrator and local login
enabled. Once it does, use the following bootstrap sequence:

1. Wait for the `bambuddy` HelmRelease and the
   `bambuddy-kanidm-oauth2-credentials` Secret to become ready.
2. Open the admin route and enable authentication. Create a strong local
   administrator; this is the break-glass account. Store its password in the
   normal operator password manager, not in Git.
3. In **Settings -> Authentication -> SSO / OIDC**, add:

   - Name: `Kanidm`
   - Issuer: `<public-kanidm-issuer>/oauth2/openid/bambuddy`
   - Client ID: `bambuddy`
   - Client secret: the `CLIENT_SECRET` value from
     `bambuddy-kanidm-oauth2-credentials`
   - Scopes: `openid email profile groups`
   - Auto-create users: enabled
   - Require verified email: enabled
   - Default group: `Administrators`

4. In a private browser session, verify the Kanidm login, callback, email
   claim, and Administrator membership. The Kanidm `scopeMap` permits only
   `ops-admins` members to authorize this client.
5. Enable OIDC autologin, then disable local password login. Verify both a new
   OIDC session and `https://bambuddy.home.kelch.io/login?fallback=local` before
   ending the bootstrap session.

The OIDC client secret is intentionally entered through Bambuddy's admin UI for
this bootstrap. Upstream also supports environment-managed OIDC configuration;
if that replaces the UI flow later, consume the kaniop-generated Secret with a
`secretKeyRef` rather than copying its value into Git. Bambuddy encrypts the
stored secret in PostgreSQL, using the key under `/app/data` on the archive PVC.

## Local-login recovery

If Kanidm or the OIDC configuration is unavailable, add the following
temporary environment variable to the app container and reconcile Flux:

```yaml
BAMBUDDY_LOCAL_LOGIN: "true"
```

Then use `/login?fallback=local` and the stored break-glass administrator.
Remove the variable and reconcile again after repairing OIDC. Do not leave the
override enabled during normal operation.

## Printer onboarding and containment check

Add the printer manually by its fixed IP. Do not enable discovery, Virtual
Printer, or Proxy mode. Run Bambuddy's connection diagnostic, upload a real
file, start one approved test job, and exercise the applicable camera and file
retrieval paths. In Hubble, verify the expected flows and any policy drops; an
observed happy path alone does not prove the allow-set.

Run explicit negative tests from the selected pod: an unapproved port on the
printer and an allowed printer port on an address outside the IoT VLAN must be
denied. An unrelated IoT host on an allowed port is expected to be reachable
under this interim `/24` policy and demonstrates the documented residual risk.
DNS and `bambuddy-db` Postgres on 5432 are the other expected flows. Add the
public issuer's narrowly scoped egress only when the OIDC prerequisite above is
implemented; the current policy deliberately contains no usable Kanidm path.

Cilium restricts Bambuddy's allow-set. It does not stop unpoliced workloads or
host-network processes from using the node-to-IoT path. UniFi is the intended
owner of the routed boundary after #348 establishes and verifies default-deny;
today it sees the masqueraded Talos node rather than the Bambuddy pod.

## Backup and restore drill

Both the CNPG data PVC and the archive PVC inherit Longhorn's daily and weekly
backup jobs. Leave Bambuddy's scheduled local backup disabled until a separate
NFS-backed backup path is mounted. A complete portable backup contains the
database and every data directory; keeping its default five retained copies
under `/app/data/backups` can consume several times the live archive capacity.

Treat every portable ZIP as a secret-bearing artifact. It includes the database
and auto-generated MFA encryption key, so possession of the ZIP permits
decryption of stored OIDC and TOTP secrets. Store it only on encrypted storage
with restricted access, and remove temporary and on-volume copies after the
restore has been verified. If `MFA_ENCRYPTION_KEY` later moves to a Kubernetes
Secret, back up that Secret separately.

Portable backups use SQLite format even when Bambuddy runs on PostgreSQL, so
they can be restored onto either backend. See upstream's
[backup and restore documentation](https://wiki.bambuddy.cool/features/backup/)
for the included directories and retention behavior.

Exercise the two recovery paths separately:

1. **Portable restore:** create a sample archive and download a portable ZIP.
   Restore it into a disposable Bambuddy instance, restart when prompted, and
   confirm the user, OIDC provider, printer configuration, history, sample
   archive, and archive files are present. Re-enter the OIDC client secret only
   if the tested release proves it is omitted, then verify a complete login.
2. **Longhorn recovery:** select CNPG and archive backups from a consistent
   recovery window. Following
   [longhorn-backup-restore](longhorn-backup-restore.md), restore the CNPG data
   volume with a CNPG-aware stop/reattach procedure and independently restore
   the archive PVC. Do not declare success after mounting only one volume.
   Start the disposable stack only after both are attached, then verify a
   database query and the expected files under `/app/data` before testing the
   full application state.
3. Record the exact CNPG stop/reattach procedure and restore result before
   making the PR ready. Delete disposable resources only after that record is
   complete.

The generic Longhorn single-PVC drill is not by itself proof of Bambuddy
recovery: the database and archive have separate storage contracts.
