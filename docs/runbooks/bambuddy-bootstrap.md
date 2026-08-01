# Bambuddy bootstrap and recovery

Bambuddy runs in `printing` with a single-instance CloudNativePG database on a
5 GiB Longhorn PVC. Archives and portable backups use a separate 100 GiB
Longhorn PVC. It is reachable only through `gateway-admin` at
`https://bambuddy.home.kelch.io`. Automatic discovery, host networking,
Virtual Printer, Proxy mode, external archive roots, and cloud integrations are
out of scope for the initial deployment.

## Before making the PR ready

1. Record the printer model, fixed IP, VLAN, and UniFi rule that permits the
   cluster pod CIDR to reach it.
2. Decide whether LAN Only/Developer Mode is acceptable for that model.
3. From a temporary pod subject to the intended policy, test the model's
   required outbound flows: MQTT/TLS `8883`, FTPS `990`, RTSPS `322`, A2L
   camera/file transfer `6000`, and A1/P1S ports `2024-2026` as applicable.
4. Replace `192.0.2.1/32` in `networkpolicy.yaml` with the fixed printer `/32`
   and remove ports the model does not use. Do not substitute its VLAN CIDR.
5. Confirm the initial 100 GiB archive allocation is reasonable. Longhorn can
   expand it later; move only bulk archives to NFS in a separately reviewed
   change.

The documentation address intentionally prevents printer connectivity while
the PR is a draft. Do not merge it unchanged.

## First login and OIDC

1. Wait for the `bambuddy` HelmRelease and the
   `bambuddy-kanidm-oauth2-credentials` Secret to become ready.
2. Open the admin route and enable authentication. Create a strong local
   administrator; this is the break-glass account. Store its password in the
   normal operator password manager, not in Git.
3. In **Settings -> Authentication -> SSO / OIDC**, add:

   - Name: `Kanidm`
   - Issuer: `https://auth.home.kelch.io/oauth2/openid/bambuddy`
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

The OIDC client secret is intentionally entered through Bambuddy's admin UI:
upstream does not expose provider bootstrap through environment variables.
Bambuddy encrypts it in PostgreSQL. The encryption key remains under
`/app/data` on the archive PVC.

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
Printer, or Proxy mode. Run Bambuddy's connection diagnostic and one approved
test operation. Use Hubble while testing to confirm that accepted egress goes
only to the printer `/32`, DNS, and `10.32.140.1:443`; denied tests to another
IoT device, a node address, the NAS, and the Kubernetes API must remain denied.

## Backup and restore drill

Both the CNPG data PVC and the archive PVC inherit Longhorn's daily and weekly
backup jobs. Configure Bambuddy's portable scheduled backup to write under
`/app/data/backups`, then create a manual backup after OIDC and printer setup.
Portable backups use SQLite format even when Bambuddy runs on PostgreSQL, so
they can be restored onto either backend.

For the acceptance drill:

1. Create a sample archive and a portable backup.
2. Restore the Longhorn backup as a new PVC following
   [longhorn-backup-restore](longhorn-backup-restore.md), or restore the
   portable backup into a disposable Bambuddy instance.
3. Confirm the user, OIDC provider, printer configuration, history, and sample
   archive are present.
4. Delete the disposable restore only after recording the drill result.

The auto-generated MFA encryption key lives under `/app/data` and is included
in Bambuddy portable backups. If it is ever moved to an environment-provided
secret, that secret must be backed up separately.
