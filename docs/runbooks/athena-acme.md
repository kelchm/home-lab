# Athena ACME certificates

## Current installation

Athena runs stock **Lego 5.6.0** with a small local DSM import hook, installed on **2026-10-31** after [#1213](https://github.com/kelchm/home-lab/pull/1213) merged as `35b8d57a64047a149ed94a3a06f02b068a4fb5cd`. It serves trusted HTTPS for `athena.home.kelch.io` on 443 and 5001 using certificate `N2RhLk`, description `athena-dsm-acme`, assigned only to DSM `system/default` and marked default. All five original unrelated service bindings are unchanged.

Task `7`, **Athena ACME renewal**, is enabled as root daily at **00:17 US/Eastern**, with task email off. Its updated command passed through DSM's native scheduler on 2026-10-31 at 19:24:17–19:24:18 EDT, exit 0. That was a manual scheduler dispatch; the first later clock-triggered execution and natural due renewal have not yet been observed. The next trigger read back as **2026-11-01 00:17 EDT / 04:17 UTC**.

The new leaf is SHA-256 `fd9d08c38e04f7da12d03a54a51b89f7fd4061065d52a6e71b58ec3b5de29810`, expiring **2027-01-29 22:23:59 UTC**. The initial run created a new Let's Encrypt account and domain key, completed a fresh DNS-01 challenge and imported the issued leaf into the existing DSM object. Both authoritative nameservers subsequently returned no challenge TXT record. Trusted checks from Athena and the Mac matched the issued leaf on both listeners; the Pi probe reports success, scrape health and the new expiry.

The Pi's DSM probe and the DSM-only **less than 14 days, held for one hour** Pushover warning remain in place; Matt had already confirmed notification delivery. This observes served TLS, availability and expiry rather than individual renewal-job failures. See [service probes](host-monitoring.md#service-probes).

The former custom runner from [#1094](https://github.com/kelchm/home-lab/issues/1094) remains on the NAS for recovery, but task `7` no longer invokes it. Its recorded source is `77eb1bc995066bca2a077f1210495e661d0eabbe`, SHA-256 `5ce06f78cc15a315963191a1211bb95066193a0c391cc8f547c4839a0a2f5b6e`; [the original procedures](https://github.com/kelchm/home-lab/blob/77eb1bc995066bca2a077f1210495e661d0eabbe/docs/runbooks/athena-acme.md) remain in Git. The complete pre-cutover runtime, task definition and acceptance receipts are retained privately on Athena under root-only `/volume1/@athena-acme-cutover-20261031T231842Z`. Those backups contain credentials and private keys; do not publish them.

## How renewal works

The [local hook](../../scripts/synology/deploy-athena-dsm.py) runs as root and calls `/usr/syno/bin/synowebapi --exec`. This uses local privilege, without a DSM password, HTTP login, temporary administrator or OTP-policy change. It refuses a different certificate ID, duplicate description, broken target or changed DSM-only/default binding. It checks the candidate's chain and hostname, stages private copies because DSM can consume import files, then replaces `N2RhLk` using the native API and string `as_default="true"`.

The [Lego configuration](../../scripts/synology/athena-lego.yml) covers only `athena.home.kelch.io`, uses Let's Encrypt, and invokes the hook after successful issuance or renewal. Its DNS checks use `1.1.1.1:53` and `1.0.0.1:53`, matching S3's resolver choice on Athena: the LAN resolver serves the split-horizon `home.kelch.io` zone and may not see the public challenge record. `noBundle: true` keeps the leaf and issuer chain separate for DSM. Lego uses its default ARI/renewal timing, retains the certificate key on renewal and writes its own account and certificate state.

There is no local inventory snapshot, rebaseline command or automatic rollback. Unrelated certificate objects are not a standing invariant. The same-ID import retained their assignments during cutover. The hook checks the managed DSM binding before and after import, while the existing TLS probe observes the served certificate. It does not maintain a transaction journal or independently verify every unrelated binding across process failures.

### Credential handling

The existing `athena-acme` Cloudflare token has `Zone:Zone:Read` and `Zone:DNS:Edit` for `kelch.io` and is shared with S3. Keep its value and permissions unchanged; do not revoke or rotate it as cleanup. The token already lives in the root-only `cloudflare-token` file.

Set **`CF_DNS_API_TOKEN_FILE=/volume1/@athena-acme/cloudflare-token`**. Lego reads that file into memory and sends HTTPS requests through its Go HTTP client. The token is not copied into a new credentials file or put into curl/wget arguments. Only its path is exported. Plain-token environment variables take precedence over `_FILE`, so leave them unset as shown in the task command below. Do not print the token or enable debug logging. This removes the command-line exposure identified in the earlier acme.sh draft; it does not prevent root from reading the token or process memory.

A stolen token would still permit DNS edits within `kelch.io`; choosing Lego does not narrow the token's existing permissions. The integration needs no DSM credentials and does not change S3's credential or renewal setup.

## Files and pins

Everything remains under root-owned `/volume1/@athena-acme`, outside the writable Docker share. Keep directories `0700` and private/configuration files `0600`; the Lego executable is `0700`. No container, cluster service, DSM account, listener, DNS exposure or S3 deployment is added.

| Path | Role |
|---|---|
| `bin/lego` | Stock pinned Linux amd64 executable for Athena's Ryzen CPU |
| `lego.yml` | Single-host configuration and local deploy command, containing no token |
| `lego-state/` | New Lego account, domain key, certificates and renewal metadata |
| `lego-state/certificates/athena.home.kelch.io.{key,crt,issuer.crt}` | Files read by the local import hook |
| `deploy-athena-dsm.py` | Local same-ID DSM import hook |
| `cloudflare-token` | Existing protected token file, read directly by Lego |
| Existing `client/`, `state/`, `certs/`, `issued/` and runner | Retired client and private recovery material |

[Preparation](../../scripts/synology/prepare-athena-acme.sh) downloads the public `lego_v5.6.0_linux_amd64.tar.gz` release and verifies SHA-256 `f02fc11b6f56b97e387fa7caa0268f86f5a336953b22458e6430e4651fea439a` before extracting only `lego`. It copies the configuration and local hook into the staging directory. It does not run an installer or contact Athena.

## Installation and daily task

Lego uses separate state rather than converting acme.sh's private configuration. The 2026-10-31 cutover disabled the old task, retained its runtime and command, installed only the new executable/configuration/hook, ran the initial order/import, verified DSM and S3, and switched the same task to the command below. No NAS reboot or Docker restart was needed. All 40 retained legacy files matched the backup; all 11 containers retained their IDs, images, start times and restart counts. S3's certificate and response were unchanged. No rollback was needed.

For a future reviewed binary/configuration update, prepare public files locally using `bash scripts/synology/prepare-athena-acme.sh <new-local-directory>`, disable task `7` and ensure it is idle before replacing those files. Retain the existing `lego-state/` and token. Do not recreate the account or force an order to test an update. Check the pinned version, private modes, ordinary run result, trusted DSM listeners, certificate assignments and S3 before re-enabling the task.

Task `7` contains:

```sh
umask 077
unset CF_API_KEY CLOUDFLARE_API_KEY CF_DNS_API_TOKEN CLOUDFLARE_DNS_API_TOKEN CF_ZONE_API_TOKEN CLOUDFLARE_ZONE_API_TOKEN
export CF_DNS_API_TOKEN_FILE=/volume1/@athena-acme/cloudflare-token
/volume1/@athena-acme/bin/lego --config /volume1/@athena-acme/lego.yml \
  >> /volume1/@athena-acme/renewal.log 2>&1
```

The task appends to the private log so a later successful or not-due run does not erase an earlier error.

Lego saves an issued certificate before running its deploy hook. A failed hook causes an error, but a later not-due run does **not** retry that hook automatically. Fix the cause and retry the saved files directly, without another DNS request or order:

```sh
/usr/bin/python3 /volume1/@athena-acme/deploy-athena-dsm.py
```

This retry needs no Cloudflare credential. Successful import recovery is manual; the existing served-TLS expiry warning remains the notification path. No new job-status alert or local retry ledger is introduced.

## A legitimate later DSM binding change

The hook has no dependency on the old `ALK0dn` object or unrelated certificate metadata. Its expiry is not a demonstrated deadline for DSM renewal. No rebaseline is required for unrelated changes. If DSM's managed certificate ID or intended assignment changes, update the small hook deliberately; do not silently create a replacement certificate or move other services onto it.

## Recovery

Disable the task and ensure the job is idle if the new path fails. Before restoring the original task command, run `chmod 0600 /volume1/@athena-acme/bin/lego`: the old runner recursively requires every file under its runtime to be `0600` and refuses an executable at `0700`. Retain the other private file/directory modes. Lego's new files and state are separate; keep both sets of account/key material and recovery bundles private.

Once its checks pass, the old runner's ordinary `run` re-imports its own retained or renewed leaf if DSM serves a different certificate. Returning to the old task can therefore replace Lego's served leaf. Verify the actual served certificate, assignments and S3 after recovery. Do not revoke the shared Cloudflare token, delete certificate objects or reboot the NAS. If returning to Lego later, restore `bin/lego` to `0700` before using it.

For certificate recovery, DSM's Certificate Settings can import a retained private key/certificate/intermediate bundle into the same managed object. That is the manual recovery path; this integration adds no automatic certificate rollback system. Verify DSM and S3 after recovery.

Use management SSH at `10.32.20.5`. These checks bypass DNS while preserving normal hostname/trust verification:

```sh
curl --resolve athena.home.kelch.io:443:10.32.20.5 https://athena.home.kelch.io/
curl --resolve athena.home.kelch.io:5001:10.32.20.5 https://athena.home.kelch.io:5001/
```

## Sources and verification limits

- [Pinned Lego 5.6.0 release](https://github.com/go-acme/lego/releases/tag/v5.6.0) and [configuration reference](https://go-acme.github.io/lego/references/ref-file/).
- [File-based credential loading](https://github.com/go-acme/lego/blob/v5.6.0/platform/env/env.go) and [in-process Cloudflare HTTP client](https://github.com/go-acme/lego/blob/v5.6.0/providers/dns/cloudflare/internal/client.go).
- [Renewal path](https://github.com/go-acme/lego/blob/v5.6.0/cmd/internal/root/process_renew.go), especially the not-due return and save-before-deploy ordering.
- [Community local Synology hook](https://github.com/zaxbux/syno-acme) and [Tailscale's local importer](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go), which use the same native API route. The alternative [lego-based Synology project](https://github.com/JessThrysoee/synology-letsencrypt) writes DSM archive files directly; this integration uses the native import approach instead.

Run `python3 scripts/ci/test-athena-acme.py` for the three small mocked import checks and ShellCheck on the preparation script. Preparation passed with the actual pinned Linux archive and private staging modes. The native Mac 5.6.0 build accepted this configuration and synthetic `_FILE` credentials against a local HTTPS fixture, failed before an order when the file was missing, and skipped both network calls and deployment for a not-due synthetic certificate with ARI disabled. The synthetic token appeared in neither output nor saved state. Live cutover subsequently established Linux/DSM execution, one fresh DNS-01 order, same-ID import and one successful native scheduler dispatch. The Cloudflare token was absent from the private log after both runs. Later unattended execution, due renewal, failed-import retry and an actual rollback through this replacement remain unexercised; they do not require forced orders or another test campaign.
