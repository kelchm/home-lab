# Athena ACME certificates

## Current installation

Athena still runs the custom native runner commissioned in [#1094](https://github.com/kelchm/home-lab/issues/1094). It serves trusted HTTPS for `athena.home.kelch.io` on 443 and 5001 using certificate `N2RhLk`, description `athena-dsm-acme`, assigned only to DSM `system/default` and marked default. The other five original service bindings were retained. Task `7`, **Athena ACME renewal**, runs as root daily at **00:17 US/Eastern**, with task email off.

The installed runner is from revision `77eb1bc995066bca2a077f1210495e661d0eabbe`, SHA-256 `5ce06f78cc15a315963191a1211bb95066193a0c391cc8f547c4839a0a2f5b6e`. Its [source and original procedures](https://github.com/kelchm/home-lab/blob/77eb1bc995066bca2a077f1210495e661d0eabbe/docs/runbooks/athena-acme.md) remain available in Git. Removing that source from the current repository does not uninstall it from Athena.

Same-ID replacement, a different issued leaf, manual rollback and return passed on 2026-10-29. The first automatic task execution passed on 2026-10-30. The last recorded current leaf is `b237b6d1…`, expiring **2027-01-27 16:20:03 UTC**. Those observations do not establish the first natural due renewal or fresh DNS challenge. [#1094](https://github.com/kelchm/home-lab/issues/1094) retains the commissioning record.

The Pi's DSM probe and the DSM-only **less than 14 days, held for one hour** Pushover warning are accepted; Matt confirmed notification delivery. That monitoring stays in place. It checks served TLS, availability and expiry, rather than individual renewal-job failures. See [service probes](host-monitoring.md#service-probes).

## Draft replacement — not installed

[#1131](https://github.com/kelchm/home-lab/issues/1131) now proposes stock **acme.sh 3.1.6** for DNS validation, renewal timing, installed files and failed-install retries, plus one [local DSM import hook](../../scripts/synology/deploy-athena-dsm.py). No NAS access, credential use, issuance or deployment was performed to prepare this draft. Merge does not deploy it; migration needs a separate decision.

The hook runs as root and calls `/usr/syno/bin/synowebapi --exec`. This uses local privilege, without a DSM password, HTTP login, temporary administrator or OTP-policy change. It refuses a different certificate ID, duplicate description, broken target or changed DSM-only/default binding. It checks the candidate's chain and hostname, stages private copies because DSM can consume import files, then replaces `N2RhLk` using the already-tested native API and string `as_default="true"`.

acme.sh's normal `--install-cert --reloadcmd` integration invokes the hook after copying the issued files. A successful initial installation records `Le_InstallCertSuccessTime`; later renewals retry a failed installation when the certificate is newer than that marker, even if another order is not due. This is upstream behavior, not a new local retry mechanism.

There is no local inventory snapshot, rebaseline command, success-marker framework or automatic rollback. Unrelated certificate objects are not a standing invariant. The same-ID import is expected to retain their assignments; verify them during migration. The hook checks the managed DSM binding before and after import, while the existing TLS probe observes the served certificate. It does not maintain a transaction journal or independently verify every unrelated binding across process failures.

### Credential tradeoff to review

This draft uses upstream `dns_cf` instead of our custom DNS adapter. The existing `athena-acme` Cloudflare token has `Zone:Zone:Read` and `Zone:DNS:Edit` for `kelch.io` and is shared with S3. Keep its value and permissions unchanged; do not revoke or rotate it as cleanup.

**Unlike the current adapter, stock `dns_cf` persists the token in acme.sh's protected configuration, and its HTTP transport can place the authorization header in curl/wget process arguments.** This draft therefore does not retain the current guarantee that the token never appears in process arguments. That difference needs to be accepted before migration. Do not print the token, pass it in an operator command line or enable debug logging. If that upstream behavior is unacceptable, choose a stock client with in-process DNS requests instead of rebuilding the DNS client here.

## Files and pins

Everything remains under root-owned `/volume1/@athena-acme`, outside the writable Docker share. Keep directories `0700` and files `0600`. No container, cluster service, DSM account, listener, DNS exposure or S3 deployment is added.

| Path | Role |
|---|---|
| `client/acme.sh`, `client/dnsapi/dns_cf.sh` | Stock pinned client and Cloudflare plugin |
| `state/`, `certs/` | Existing ACME account, domain key, configuration and renewal metadata |
| `issued/{key,cert,chain,fullchain}.pem` | Files installed by acme.sh |
| `deploy-athena-dsm.py` | Local same-ID DSM import hook |
| `cloudflare-token` | Existing protected token file; preserve for recovery |

[Preparation](../../scripts/synology/prepare-athena-acme.sh) downloads only public files from acme.sh source commit `807da6498377ee5e0cf43a78091f46f12dc59a89` and verifies their SHA-256 hashes. It also copies the local hook. It does not run the upstream installer or contact Athena.

## Proposed migration

These are draft operator steps, not completed work or authorization to execute. Reuse the existing Let's Encrypt account, domain key and certificate. No forced order is needed to register and test the import hook.

1. Resolve the credential tradeoff above. Disable task `7` and ensure the old job is idle. Retain a private backup of the existing runtime and task command for recovery. Check the current DSM certificate and unrelated bindings, management SSH and S3 health.
2. Run `bash scripts/synology/prepare-athena-acme.sh <new-local-directory>`. After the source is approved, transfer the prepared public client files and hook into the existing protected runtime. Preserve `state/`, `certs/`, `issued/`, keys and token. Do not run the old wrapper against the modified client tree: its hash/file-list guard deliberately rejects it.
3. In `certs/athena.home.kelch.io_ecc/athena.home.kelch.io.conf`, change only `Le_Webroot='dns_athena'` to `Le_Webroot='dns_cf'`. Provision `SAVED_CF_Token` in protected `state/account.conf` from the existing token file locally, without printing or passing its value in arguments. Preserve the account, key, CA, renewal timestamps and remaining settings. Review those private edits on the NAS, not in a transcript.
4. As root on Athena, register the standard installation and local reload command below. This imports the existing issued certificate and can briefly reload DSM's web service; it does not request a new order. Require success before enabling the task, including the installation-success timestamp needed by upstream retry handling.

```sh
umask 077
/bin/sh /volume1/@athena-acme/client/acme.sh \
  --home /volume1/@athena-acme/client \
  --config-home /volume1/@athena-acme/state \
  --cert-home /volume1/@athena-acme/certs \
  --install-cert -d athena.home.kelch.io --ecc \
  --key-file /volume1/@athena-acme/issued/key.pem \
  --cert-file /volume1/@athena-acme/issued/cert.pem \
  --ca-file /volume1/@athena-acme/issued/chain.pem \
  --fullchain-file /volume1/@athena-acme/issued/fullchain.pem \
  --reloadcmd '/usr/bin/python3 /volume1/@athena-acme/deploy-athena-dsm.py'
```

5. Verify trusted access on 443 and 5001, the retained certificate ID/default/DSM-only assignment, unchanged unrelated assignments and S3. The [DNS-independent checks](#recovery) retain the canonical hostname. If the hook fails, leave the task disabled and inspect it; repeat the installation command after correction, without forcing issuance.
6. Change the existing task's command to the following, retaining its root owner, daily 00:17 schedule and email-off setting. Run once, inspect its result and private log, then enable it. A normal not-due result is sufficient for cutover; record the first natural renewal when it occurs.

```sh
umask 077
/bin/sh /volume1/@athena-acme/client/acme.sh --cron \
  --home /volume1/@athena-acme/client \
  --config-home /volume1/@athena-acme/state \
  --cert-home /volume1/@athena-acme/certs \
  > /volume1/@athena-acme/renewal.log 2>&1
```

## A legitimate later DSM binding change

The draft hook has no dependency on the old `ALK0dn` object or unrelated certificate metadata. Its expiry is not a demonstrated deadline for DSM renewal. No rebaseline is required for unrelated changes. If DSM's managed certificate ID or intended assignment changes, update the small hook deliberately; do not silently create a replacement certificate or move other services onto it.

## Recovery

Disable the task if the new path fails. The retained pre-migration runtime and original task command restore the previous setup; the old runner needs its original client files and configuration, not only its Python file. Keep ACME account/key material and recovery bundles private. After any new issuance, review newer renewal/account state before restoring an older copy. Do not revoke the shared Cloudflare token, delete certificate objects or reboot the NAS.

For certificate recovery, DSM's Certificate Settings can import a retained private key/certificate/intermediate bundle into the same managed object. That is the manual recovery path; the draft adds no automatic certificate rollback system. Verify DSM and S3 after recovery.

Use management SSH at `10.32.20.5`. These checks bypass DNS while preserving normal hostname/trust verification:

```sh
curl --resolve athena.home.kelch.io:443:10.32.20.5 https://athena.home.kelch.io/
curl --resolve athena.home.kelch.io:5001:10.32.20.5 https://athena.home.kelch.io:5001/
```

## Sources and draft checks

- [Pinned acme.sh core](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/acme.sh), especially `renew`, `_ensure_install` and `_installcert`.
- [Stock Cloudflare DNS plugin](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/dnsapi/dns_cf.sh).
- [acme.sh Synology guide](https://github.com/acmesh-official/acme.sh/wiki/Synology-NAS-Guide); its HTTP-login deployment hook is replaced here by the local root command.
- [Tailscale's local Synology importer](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go), which uses the same local API route.

Run `python3 scripts/ci/test-athena-acme.py` for the small mocked import checks and ShellCheck on the preparation script. The tests establish argument handling, target refusal and API-failure propagation; they do not establish live DSM import or migration. DNS-01, stock-plugin credential handling, ordinary due renewal and recovery on Athena remain untested for this draft.
