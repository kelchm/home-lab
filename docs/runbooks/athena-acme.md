# Athena ACME certificates

Prepared for [#1094](https://github.com/kelchm/home-lab/issues/1094); **not installed or activated**. This runbook describes the intended native issuance and DSM import path. Merge alone does not install it. Record commissioning evidence and remaining acceptance gates in the issue.

## Scope and verified starting state

Athena should serve trusted HTTPS for **`athena.home.kelch.io` only**, on its existing 443 and 5001 listeners. No wildcard, additional SAN, listener, redirect, public exposure or trust-store change is needed. DNS-01 creates temporary `_acme-challenge.athena.home.kelch.io` TXT values; it does not require public access to DSM. The issued name will appear in public certificate-transparency logs.

Read-only checks on 2026-10-29 found DSM **7.4.1-90080 Update 1**. The canonical name resolved to **10.32.10.5**; the management address is **10.32.20.5**. Both 443 and 5001 served a Synology Inc. CA-issued leaf with only `DNS:synology`, valid until 2027-01-03. Normal trust verification and canonical-hostname validation fail. Port 443 serves a JavaScript landing page which moves the browser to 5001; `curl -L` does not exercise this transition. HTTPS redirection is currently disabled; changing that setting is outside this operation.

The original certificate **`ALK0dn`** is default and is assigned to DSM Desktop Service (`system/default`), Hyper Backup Vault, KMIP, FTPS, Synology Storage Console Server and Replication Service. Retain the original object. Move only DSM Desktop Service and the required default marker during commissioning; preserve each other explicit service binding. A missing or different baseline requires review before proceeding.

Intended reuse is deliberately limited:

| Consumer | Issuance and private state | Proposed change |
|---|---|---|
| DSM, `athena.home.kelch.io` | Native pinned acme.sh, separate account/key/state; DSM object `athena-dsm-acme` | New path in this runbook |
| S3, `s3-storage.home.kelch.io:9000` | Existing Traefik DNS-01 client and `acme.json` | Share the DNS credential only; preserve its certificate, container and configuration |
| Other DSM services and aliases | Existing assignments | Preserve; no migration or additional names |

`nas.home.kelch.io` returned NXDOMAIN in the earlier inspection. Do not add it merely because an architecture document calls it a recovery alias. A future consumer requires its own justified name, deployment adapter and review. This is an Athena ACME foundation, not an automatic wildcard or service migration framework.

## Implementation and compatibility boundary

[`athena-acme.py`](../../scripts/synology/athena-acme.py) coordinates a root-only native client and DSM's local certificate API. [`prepare-athena-acme.sh`](../../scripts/synology/prepare-athena-acme.sh) prepares only the public client files on the operator machine. No doco-cd target or Flux resource installs either tool.

The client is **acme.sh 3.1.6**, annotated tag object `c6e7807f8e2573bf8b02b71da186b4022afafa56`, source commit **`807da6498377ee5e0cf43a78091f46f12dc59a89`**. Only its core and Cloudflare DNS hook are installed. Their SHA-256 hashes are checked during preparation and execution. The runner invokes issuance/renewal and stable `--install-cert` output paths; it registers no deployment or reload hook. There is no upstream installer, cron installation, profile edit or automatic upgrade. Review client updates together with the DNS hook and wrapper, then rerun offline tests and the relevant compatibility checks.

DSM import uses `/usr/syno/bin/synowebapi --exec` as root with `SYNO.Core.Certificate/import` v1 and protected staged `key_tmp`, `cert_tmp` and `inter_cert_tmp` files. Creation omits the certificate ID; replacement uses the recorded exact ID. API output must contain `success: true`; a zero process exit is insufficient. The runner then checks assignments and verifies both live TLS listeners independently. This uses DSM's own import operation without writing its generated certificate/nginx tree or invoking a service restart.

Read-only local `Certificate.CRT/list` succeeded as `SYSTEM_ADMIN`, and Athena's installed API descriptor advertises import v1. The local import pattern is also used by [Tailscale's maintained client](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go). This is an internal interface without an established public Synology support contract. **No import or reload has been tested on this DSM build.** Its exact interruption and service effects are commissioning gates. If the API changes, fail and review compatibility; retain manual Certificate Settings import as recovery, without adding a generated-file fallback or broad restart.

This design creates **no DSM account**, stores no DSM administrator password and does not read or modify OTP policy. The upstream `synology_dsm` deployment hook, including its temporary-administrator and OTP workaround code, is absent from the installed client tree.

## Files and credential

Everything resides under **`/volume1/@athena-acme`**, owned by root. Directories are `0700`, files `0600`; the Python runner is invoked explicitly through `/usr/bin/python3`. Set `umask 077`. Protect the account key, certificate private keys, token, generated shell configuration, logs and rollback bundles together. Do not expose this directory through a share, container mount, artifact upload or unencrypted backup. Do not put secret values in command arguments, shell history, debug traces or task email. acme.sh persists the Cloudflare token in its private configuration as well as the input file.

The existing Cloudflare token has `Zone:DNS:Edit` and `Zone:Zone:Read`, scoped to `kelch.io`, with no expiry or IP filter. These permissions allow changes across the zone, not only the challenge record. Reuse preserves its exact value and permissions. The clients keep separate ACME accounts and certificate keys. **Revoking the shared token breaks both DSM and S3 renewal.** Do not rotate, revoke, broaden or recreate it as part of this change.

The current Cloudflare label and 1Password title are **`athena-s3-storage-acme`**. The intended shared name is **`athena-acme`**; no rename has occurred. The stable reference is `op://Project - home-lab/yg4lgp23nzgmknxb7bgn3rfsvm/credential`. The S3 runtime consumes its encrypted dotenv value, not the title, so a metadata-only rename needs no S3 redeployment.

During the approved operation, use Matt's Cloudflare administrator browser session to rename only that token's label; the token's existing zone permissions need not and may not permit editing its own metadata. Do not request broader permissions just for the label. Rename the existing 1Password item by its ID, preserving all fields, then check only list metadata for the ID/title. Check Cloudflare's displayed permissions, zone, expiry and IP-filter metadata before and after. Use the ID reference for consumers, and update the maintained S3 credential paragraph to the verified shared title. If the browser session is unavailable, retain the current labels and record the naming gate in #1094; do not claim it was completed.

## Approval and installation

Ask Matt once, after review of the exact revision, to approve: issuance for the single hostname using the existing token; creating DSM object `athena-dsm-acme`; moving DSM's binding/default while preserving `ALK0dn` and unrelated bindings; native root job installation and bounded notification tests; the metadata-only naming cleanup; and a possible brief DSM web interruption. No account creation/elevation/deletion or OTP change is involved. Keep an SSH recovery session open. S3 deployment and other service operations remain outside this approval.

After approval, prepare public files from the reviewed checkout locally:

```sh
bash scripts/synology/prepare-athena-acme.sh /tmp/athena-acme-stage
cp scripts/synology/athena-acme.py /tmp/athena-acme-stage/
ssh kelchm@10.32.20.5 'umask 077; mkdir /var/tmp/athena-acme-stage'
scp -r /tmp/athena-acme-stage/. kelchm@10.32.20.5:/var/tmp/athena-acme-stage/
ssh kelchm@10.32.20.5 '
  set -eu
  sudo -n test ! -e /volume1/@athena-acme
  sudo -n install -d -m 0700 -o root -g root /volume1/@athena-acme /volume1/@athena-acme/client /volume1/@athena-acme/client/dnsapi
  sudo -n install -m 0600 -o root -g root /var/tmp/athena-acme-stage/athena-acme.py /volume1/@athena-acme/athena-acme.py
  sudo -n install -m 0600 -o root -g root /var/tmp/athena-acme-stage/client/acme.sh /volume1/@athena-acme/client/acme.sh
  sudo -n install -m 0600 -o root -g root /var/tmp/athena-acme-stage/client/dnsapi/dns_cf.sh /volume1/@athena-acme/client/dnsapi/dns_cf.sh
'
```

Check every created directory's ownership and mode before provisioning secrets. `/volume1` was root:root `0755`; its `docker` share was `0777` with a DSM ACL, so private native state lives outside that share. Every ancestor must remain a root-owned directory without group/world write access. Leave the existing share and its permissions alone. Refuse an existing runtime tree or staging directory until its contents and ownership have been reviewed. Never recursively chmod/chown unrelated state.

Provision the existing token directly into a new protected file. This pipeline sends its value only through stdin; it produces no token output and refuses an existing file or symlink:

```sh
op read 'op://Project - home-lab/yg4lgp23nzgmknxb7bgn3rfsvm/credential' |
  ssh kelchm@10.32.20.5 'sudo -n /usr/bin/python3 -c '\''import os,sys; p="/volume1/@athena-acme/cloudflare-token"; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); f=os.fdopen(fd,"w"); f.write(sys.stdin.read()); f.close()'\'''
```

Do not run `op read` by itself or inspect the full item. Do not decrypt S3's runtime env or ACME account as a shortcut. Remove only the public staging directories after installation. The native runner creates its remaining protected state as needed. Verify installed hashes through `inspect` before any issuance.

## Commissioning

All commands below execute on Athena as root through `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py <command>`. They are manual steps in an approved operation, not an automatic post-merge hook.

1. **Capture the baseline.** Run `inspect`. Retain the sanitized certificate IDs/defaults/service map in #1094. Confirm original `ALK0dn` and all six original bindings, no object named `athena-dsm-acme`, and accessible management SSH. Record S3 container IDs/start times/restart counts, health and trusted public leaf fingerprint using read-only inspection. Confirm NAS system time, outbound HTTPS/DNS and normal public CA trust. Renewal's live checks connect to loopback with canonical SNI, avoiding dependence on the cluster's hostname answer.
2. **Issue once.** Run `issue`. This registers a separate Let's Encrypt account and creates/removes its own challenge TXT value with the existing token. Confirm only the canonical SAN, matching private key, trusted chain and sufficient validity. A successful issue does not mean DSM is serving it. On failure preserve the private state, identify any specific leftover challenge value, and clean up only that value after approval; never delete another client's TXT value. Do not force repeated production issuance.
3. **Import a new object.** Run `import-new`. It must retain the original object, assignments and default before any UI change. Record the returned and re-read new certificate ID. If import response or postchecks fail, stop; inspect both DSM's inventory and the private state before retrying, because the API may have applied a change despite a lost response.
4. **Bind only DSM.** In DSM Control Panel → Security → Certificate → Settings, assign `athena-dsm-acme` to DSM Desktop Service (`system/default`) and establish the intended default marker if required by DSM. Explicitly retain every unrelated service on `ALK0dn`. Do not replace the contents of `ALK0dn`, reset certificates or delete the original. Record the complete resulting map. Permit a short DSM web-session disruption and measure it; do not promise a zero-interruption import.
5. **Accept the baseline.** Run `accept`. Require exact single-name identity, normally trusted live leaves matching the issued certificate on both 443 and 5001, only the approved binding/default move, and retained original object. Recheck S3 health, certificate and container identity/start/restart metadata. If anything unrelated changed, stop before enabling scheduled import and review the effect.
6. **Exercise import and the no-renewal path.** Run `reimport` to apply the accepted bundle through the same guarded native import path used by renewal; verify both listeners and the unchanged map. Then run `run` twice. Both must verify the accepted mapping and live certificate, and write success only after all checks. A not-due client return is expected; it must not trigger a new forced order. `watchdog` must then pass. A same-certificate run may skip an unnecessary import. Neither a repeat import nor a not-due run is a genuine renewal; do not use `--force` issuance to simulate one.
7. **Verify ordinary access.** From a normal client, open `https://athena.home.kelch.io/`, observe its landing transition and verify the trusted `:5001` UI. Check both URLs with normal curl verification. Verify the management-address override below. Record TLS identity/expiry and the actual interruption. Only after acceptance, update Homepage's `href` and `siteMonitor` together to `https://athena.home.kelch.io:5001` and commit the verified current-state documentation.

Do not proceed to scheduling until failed issuance/import can be diagnosed without publishing private logs, and the notification route below has delivered its test.

## Scheduling, failure notification and expiry

In DSM Task Scheduler, create two **root / user-defined script / daily** tasks, initially disabled. DSM currently uses `US/Eastern`. Choose and record daily local times: **00:17 renewal and 01:17 watchdog** give 04:17/05:17 UTC during daylight time and 05:17/06:17 UTC in standard time. The 26-hour freshness gate accommodates the 25-hour interval when clocks fall back. Use the local times consistently rather than claiming a fixed year-round UTC schedule:

| Task | Daily local time (`US/Eastern`) | Script |
|---|---|---|
| Athena ACME renewal | 00:17 | `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py run` |
| Athena ACME watchdog | 01:17 | `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py watchdog` |

Enable **Send run details by email**, **Send run details only when the script terminates abnormally**, and Matt's verified existing notification recipient. The wrapper emits a sanitized failure summary and returns nonzero for client, import, binding, trust, expiry or stale-run failure; detailed command output remains private. No failure may be hidden by a trailing successful shell command. Check DSM's actual email configuration interactively; configured UI fields alone do not establish delivery.

After approval, use a temporary root task with the same recipient and abnormal-only email options and script `printf '%s\n' 'Athena ACME notification commissioning test'; exit 1`. Confirm the message arrives, then delete only that disposable task. Do not publish credentials or raw ACME/API output in task email. Run both real tasks manually using DSM's Run action; confirm successful task status and the fresh verified-success marker. Enable their daily schedules and confirm their next-run times. Record the first automatic not-due run separately from the manual test.

The daily runner lets the pinned client decide when renewal is due; it never requests daily forced renewals. Stable output is refreshed even on not-due runs. If issuance succeeded but import failed, the existing issued bundle is retried instead of requesting another certificate. The watchdog verifies the served certificates and requires the last fully successful run within **26 hours**. Both tasks require more than **21 days** of validity; failures notify well before expiry.

These checks and DSM email delivery work independently of Kubernetes. They still depend on NAS scheduling and outbound mail. A stopped scheduler or NAS cannot notify through its own tasks. Independent off-host TLS probes are a separate root-coordinated monitoring change, outside this thread's cluster/repository scope; track their disposition in #1094. Do not claim complete scheduler-outage coverage or emit an unread `.prom` file: the current NAS exporter has no textfile collector enabled.

The first real scheduled production renewal remains pending until due. Record its new certificate fingerprint/expiry, successful same-ID import, unchanged bindings and normally verified live leaves. Do not close #1094 merely because the PR merged, a not-due run passed or a repeat import worked.

## Recovery and rollback

Retain SSH at **10.32.20.5**. A DNS-independent, fully verified client check keeps the hostname while overriding only the connection address:

```sh
curl --resolve athena.home.kelch.io:443:10.32.20.5 https://athena.home.kelch.io/
curl --resolve athena.home.kelch.io:5001:10.32.20.5 https://athena.home.kelch.io:5001/
```

An ordinary raw-IP browser URL still has a name mismatch. For trusted browser recovery, temporarily map the canonical name to the management address on that client. Preserve the existing IP fallback as an emergency route, with that limitation explicit. No cluster service is needed by the native renewal client or its loopback verification.

For initial activation rollback, disable both scheduled tasks. In DSM Certificate Settings restore DSM Desktop Service and the default marker to **`ALK0dn`**, checking the saved original map. This returns the known trust/name warning while recovering the original path. Retain the new object and private client state for diagnosis. Restore Homepage's previous link only if necessary. Do not delete/revoke certificates, reset DSM certificates, restart services or modify S3 state.

For a later bad renewal, disable the tasks, then use `rollback` to import the protected last-known-good bundle into the same recorded `athena-dsm-acme` object through DSM's API. It must preserve the accepted map and verify the previous trusted leaf on both listeners. Do not roll back ACME account/renewal metadata or revoke either certificate. Investigate the newer candidate before re-enabling `run`, which would otherwise try to apply it again. If no valid previous trusted bundle exists, restore the original assignment through DSM and accept the temporary warning. A failed rollback needs operator investigation, not a reboot or generated-file rewrite.

Metadata naming rollback changes only the Cloudflare label and 1Password title back to `athena-s3-storage-acme`. The stable item-ID reference and both runtime token values remain valid. Never revoke the shared token as cleanup for a DSM failure.

## Sources and checks

- [Let's Encrypt DNS-01](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge): private web servers can prove domain control through TXT records.
- [Pinned acme.sh core](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/acme.sh) and [Cloudflare DNS hook](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/dnsapi/dns_cf.sh): exact client behavior and protected token persistence.
- [acme.sh installation guidance](https://github.com/acmesh-official/acme.sh/tree/807da6498377ee5e0cf43a78091f46f12dc59a89#3-install-the-cert-to-apachenginx-etc): use stable installed outputs instead of depending on internal certificate files.
- [Tailscale native Synology import implementation](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go): JSON argument serialization, root execution, protected staging, omit ID for create and use ID for replacement. Its tailnet issuance and selection logic are not used here.
- [Synology certificate settings](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/connection_certificate?version=7) and [Task Scheduler](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_taskscheduler?version=7): supported operator UI for binding and failure-email configuration.

Run `python3 scripts/ci/test-athena-acme.py` and ShellCheck on the preparation script before publishing changes. Offline mocks establish guard behavior, not successful DNS issuance, DSM import, reload scope, mail delivery or actual renewal. Keep those observations distinct in #1094.
