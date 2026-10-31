# Athena ACME certificates

Implemented for [#1094](https://github.com/kelchm/home-lab/issues/1094); **trusted access, supervised renewal/import/rollback, the first automatic daily execution and central monitoring are verified**. The native client and protected credential are installed. The new certificate serves only DSM Desktop Service and is default; the other five original services retain `ALK0dn`. Both 443 and 5001 serve the issued, normally trusted leaf on the canonical and management addresses. Certificate tests and task commissioning passed on 2026-10-29; the first scheduled execution succeeded on 2026-10-30. Root task `7`, `Athena ACME renewal`, remains enabled daily at 00:17 US/Eastern with email off. The Pi DSM probe, DSM-specific expiry warning and delivered Pushover test are accepted. No watchdog or email-test task was created. The first natural due/fresh-DNS/ARI renewal remains pending; merging these scripts does not install a runner update.

## Scope and verified starting state

Athena should serve trusted HTTPS for **`athena.home.kelch.io` only**, on its existing 443 and 5001 listeners. No wildcard, additional SAN, listener, redirect, public exposure or trust-store change is needed. DNS-01 creates temporary `_acme-challenge.athena.home.kelch.io` TXT values; it does not require public access to DSM. The issued name will appear in public certificate-transparency logs.

Read-only checks on 2026-10-29 found DSM **7.4.1-90080 Update 1**. The canonical name resolved to **10.32.10.5**; the management address is **10.32.20.5**. Both 443 and 5001 served a Synology Inc. CA-issued leaf with only `DNS:synology`, valid until 2027-01-03. Normal trust verification and canonical-hostname validation failed for that original leaf. Port 443 serves a JavaScript landing page which moves the browser to 5001; `curl -L` does not exercise this transition. HTTPS redirection is currently disabled; changing that setting is outside this operation.

Before activation, the original certificate **`ALK0dn`** was default and was assigned to DSM Desktop Service (`system/default`), Hyper Backup Vault, KMIP, FTPS, Synology Storage Console Server and Replication Service. Retain the original object. Move only DSM Desktop Service and the required default marker during commissioning; preserve each other explicit service binding. A missing or different baseline requires review before proceeding.

Intended reuse is deliberately limited:

| Consumer | Issuance and private state | Proposed change |
|---|---|---|
| DSM, `athena.home.kelch.io` | Native pinned acme.sh, separate account/key/state; DSM object `athena-dsm-acme` | New path in this runbook |
| S3, `s3-storage.home.kelch.io:9000` | Existing Traefik DNS-01 client and `acme.json` | Share the DNS credential only; preserve its certificate, container and configuration |
| Other DSM services and aliases | Existing assignments | Preserve; no migration or additional names |

`nas.home.kelch.io` returned NXDOMAIN in the earlier inspection. Do not add it merely because an architecture document calls it a recovery alias. A future consumer requires its own justified name, deployment adapter and review. This is an Athena ACME foundation, not an automatic wildcard or service migration framework.

## Implementation and compatibility boundary

[`athena-acme.py`](../../scripts/synology/athena-acme.py) coordinates a root-only native client and DSM's local certificate API. [`prepare-athena-acme.sh`](../../scripts/synology/prepare-athena-acme.sh) prepares only the public client files on the operator machine. No doco-cd target or Flux resource installs either tool.

The client is **acme.sh 3.1.6**, annotated tag object `c6e7807f8e2573bf8b02b71da186b4022afafa56`, source commit **`807da6498377ee5e0cf43a78091f46f12dc59a89`**. Only the upstream core and the repository-owned `dns_athena` adapter are installed. Their SHA-256 hashes are checked during preparation and execution. The adapter accepts only the canonical Athena challenge name and operates on matching TXT values through Python HTTPS; it preserves simultaneous values from other clients. The runner invokes issuance/renewal and stable `--install-cert` output paths; it registers no deployment or reload hook. There is no upstream installer, cron installation, profile edit or automatic upgrade. Review client updates together with the local DNS adapter and wrapper, then rerun offline tests and the relevant compatibility checks.

DSM import uses `/usr/syno/bin/synowebapi --exec` as root with `SYNO.Core.Certificate/import` v1 and protected staged `key_tmp`, `cert_tmp` and `inter_cert_tmp` files. Creation omits the certificate ID and optional `as_default` field; replacement uses the recorded exact ID and JSON string `"true"` to preserve the accepted default. Athena's native parameter checker requires this field to be a string: the first repeat-import test with JSON boolean `true` was rejected with API error 5511 and an explicit string-type mismatch in the DSM log. The public UI labels 5511 “Invalid private key,” which alone does not diagnose this failure. Nondefault creation omits the optional field, following the pinned upstream hook; it never sends string `"false"`. API output must contain `success: true`; a zero process exit is insufficient. The runner then checks assignments and allows up to about 60 seconds for both live TLS listeners to serve the expected trusted leaf. This uses DSM's own import operation without writing its generated certificate/nginx tree or invoking a service restart. Initial DSM/default selection is a one-time operator UI step; unattended renewal must retain the managed certificate’s accepted bindings and preserve the complete current binding map across each import.

Read-only local `Certificate.CRT/list` succeeded as `SYSTEM_ADMIN`, and Athena's installed API descriptor advertises import v1. The local import pattern is also used by [Tailscale's maintained client](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go). This is an internal interface without an established public Synology support contract. **Initial object creation, candidate/acceptance recovery, string-typed default preservation, same-ID replacement by a different leaf, live rollback and return to the newer leaf were observed on this DSM build.** Creation used the original runner's string `"false"`; omission-based creation has not been exercised live. An operator may have selected the default manually, so the import API is not established as the cause of the initial guard stop. Focused TLS availability samples during reimport, renewal and rollback observed no failures; the accepted service map and S3 identities/start times/restarts/health/trusted leaf remained unchanged. These observations do not guarantee zero interruption or establish every package's internal reload behavior. `inter_cert_tmp` comes from the separately inspected local-hook implementation, rather than the Tailscale reference. If DSM ignores that field and the served chain fails verification, stop and review passing the full chain as `cert_tmp`; do not install an unreviewed automatic fallback. If the API changes, fail and review compatibility; retain manual Certificate Settings import as recovery, without adding a generated-file fallback or broad restart.

This design creates **no DSM account**, stores no DSM administrator password and does not read or modify OTP policy. The upstream `synology_dsm` deployment hook, including its temporary-administrator and OTP workaround code, is absent from the installed client tree.

## Files and credential

Everything resides under **`/volume1/@athena-acme`**, owned by root. Directories are `0700`, files `0600`; the Python runner is invoked explicitly through `/usr/bin/python3`. Set `umask 077`. Protect the account key, certificate private keys, token, generated shell configuration, logs and rollback bundles together. After verified success the wrapper retains only the current good and previous certificate bundles; acme.sh also maintains its own protected renewal state and backup. Do not expose this directory through a share, container mount, artifact upload or unencrypted backup. Do not put secret values in command arguments, shell history, debug traces or task email. The local DNS adapter reads the token directly from `cloudflare-token` and sends it in an in-process HTTPS header. It is not passed to acme.sh, saved in its shell configuration, or placed in curl/process arguments. The stock upstream `dns_cf` hook passes bearer headers to curl in argv, so it is deliberately excluded. Public challenge name/TXT data may appear in helper arguments.

The existing Cloudflare token has `Zone:DNS:Edit` and `Zone:Zone:Read`, scoped to `kelch.io`, with no expiry or IP filter. These permissions allow changes across the zone, not only the challenge record. Reuse preserves its exact value and permissions. The clients keep separate ACME accounts and certificate keys. **Revoking the shared token breaks both DSM and S3 renewal.** Do not rotate, revoke, broaden or recreate it as part of this change.

The Cloudflare label and existing 1Password item's title are **`athena-acme`**, renamed on 2026-10-29 from `athena-s3-storage-acme`. The token ID, value and effective permissions were preserved; Cloudflare regenerated its opaque policy identifier during the label edit. The stable reference is `op://Project - home-lab/yg4lgp23nzgmknxb7bgn3rfsvm/credential`. The S3 runtime consumes its encrypted dotenv value, not the title, so the metadata-only rename required no S3 redeployment.

Naming cleanup is complete; do not repeat it during commissioning recovery. For a future approved metadata change, use Matt's Cloudflare administrator browser session; the token's existing zone permissions need not and may not permit editing its own metadata. Do not request broader permissions just for a label. Address the existing 1Password item by ID and check only list metadata. Compare Cloudflare's effective permissions, zone, expiry and IP-filter metadata before and after, preserving the token value and consumer references.

## Approval and installation

Matt approved the original installation, first issuance, object creation, DSM/default assignment, native root tasks, bounded notification test, metadata-only naming cleanup and possible brief DSM web interruption. Naming, first issuance and the intended assignment are complete; do not request or repeat them during recovery. Matt subsequently approved recovery/acceptance and one supervised additional order with live rollback/return, then exact correction revision `398ecf6676997c9e585605b4818944cacea17e07`. Those certificate tests have passed; the installed runner and evidence are recorded below. No additional order is authorized by that completed test. Matt selected the existing Grafana/Pushover route for certificate health/expiry monitoring. The one native renewal task with email off is now commissioned; no watchdog task or disposable email test is needed. The central DSM probe and DSM-specific expiry warning are now commissioned; their acceptance and limits are recorded below. No account creation/elevation/deletion or OTP change is involved. Keep an SSH recovery session open. S3 deployment and other service operations remain outside this approval.

These operator blocks use Bash; enter `bash` first if the interactive shell is fish. After approval, set `approved_revision` to the exact approved commit SHA, confirm a clean matching checkout, and prepare public files locally:

```bash
set -euo pipefail
approved_revision='<approved-commit-sha>'
test "$(git rev-parse HEAD)" = "$approved_revision"
test -z "$(git status --porcelain)"
bash scripts/synology/prepare-athena-acme.sh /tmp/athena-acme-stage
cp scripts/synology/athena-acme.py /tmp/athena-acme-stage/
ssh kelchm@10.32.20.5 '
  set -eu
  sudo -n test ! -e /volume1/@athena-acme
  sudo -n install -d -m 0700 -o root -g root /volume1/@athena-acme /volume1/@athena-acme/client /volume1/@athena-acme/client/dnsapi
'
ssh kelchm@10.32.20.5 'sudo -n install -m 0600 -o root -g root /dev/stdin /volume1/@athena-acme/athena-acme.py' < /tmp/athena-acme-stage/athena-acme.py
ssh kelchm@10.32.20.5 'sudo -n install -m 0600 -o root -g root /dev/stdin /volume1/@athena-acme/client/acme.sh' < /tmp/athena-acme-stage/client/acme.sh
ssh kelchm@10.32.20.5 'sudo -n install -m 0600 -o root -g root /dev/stdin /volume1/@athena-acme/client/dnsapi/dns_athena.sh' < /tmp/athena-acme-stage/client/dnsapi/dns_athena.sh
```

Before provisioning any secret, compare the installed runner to the approved Git object, not just the staging copy. Both commands must print the same SHA-256; record the approved revision and hash in #1094. `inspect` checks the client and adapter pins but cannot independently establish its own approved source.

```bash
git show "${approved_revision}:scripts/synology/athena-acme.py" | shasum -a 256
ssh kelchm@10.32.20.5 'sudo -n sha256sum /volume1/@athena-acme/athena-acme.py'
```

Check every created directory's ownership and mode before provisioning secrets. `/volume1` was root:root `0755`; its `docker` share was `0777` with a DSM ACL, so private native state lives outside that share. Every ancestor must remain a root-owned directory without group/world write access. Leave the existing share and its permissions alone. Refuse an existing runtime tree or local staging directory until its contents and ownership have been reviewed. Never recursively chmod/chown unrelated state.

Provision the existing token directly into a new protected file. This pipeline sends its value only through stdin; it produces no token output and refuses an existing file or symlink. The runner validates all stdin before creating the file, so a failed or locked `op read` leaves no empty token file blocking a retry:

```bash
set -o pipefail
op read 'op://Project - home-lab/yg4lgp23nzgmknxb7bgn3rfsvm/credential' |
  ssh kelchm@10.32.20.5 'sudo -n /usr/bin/python3 /volume1/@athena-acme/athena-acme.py provision-token'
```

Do not run `op read` by itself or inspect the full item. Do not decrypt S3's runtime env or ACME account as a shortcut. Remove only the local public staging directory after installation. SSH stdin transfer avoids depending on DSM's share-scoped SFTP view. The native runner creates its remaining protected state as needed. Verify installed hashes through `inspect` before any issuance.

## Update or revert the installed runner

Use this procedure only after approval of the exact new revision; the fresh-install block above deliberately refuses an existing tree. Disable the renewal task and confirm no runner, client or helper is active, including an orphan from an interrupted operation. Keep management SSH available and confirm the protected operation lock can be acquired without waiting. First run the installed `inspect`, compare the complete DSM map and S3 baseline, and verify the protected saved `baseline.json` matches the original pre-import map; do not replace that file. Stop on any mismatch.

Set `approved_revision` to the exact approved commit and confirm a clean matching checkout. Compare the existing installed runner with its previously recorded SHA-256 before writing. Transfer **only the public runner**; preserve the client, adapter, token, keys, issued files and protected JSON state:

```bash
set -euo pipefail
previous_revision='<previously-installed-commit-sha>'
previous_runner_sha256="$(git show "${previous_revision}:scripts/synology/athena-acme.py" | shasum -a 256 | awk '{print $1}')"
test "$(ssh kelchm@10.32.20.5 'sudo -n sha256sum /volume1/@athena-acme/athena-acme.py' | awk '{print $1}')" = "$previous_runner_sha256"
git show "${approved_revision}:scripts/synology/athena-acme.py" |
  ssh kelchm@10.32.20.5 'sudo -n install -m 0600 -o root -g root /dev/stdin /volume1/@athena-acme/athena-acme.py'
expected_runner_sha256="$(git show "${approved_revision}:scripts/synology/athena-acme.py" | shasum -a 256 | awk '{print $1}')"
installed_runner_sha256="$(ssh kelchm@10.32.20.5 'sudo -n sha256sum /volume1/@athena-acme/athena-acme.py' | awk '{print $1}')"
test "$expected_runner_sha256" = "$installed_runner_sha256"
ssh kelchm@10.32.20.5 'sudo -n /usr/bin/python3 /volume1/@athena-acme/athena-acme.py inspect'
```

The two hashes must agree before executing the new runner. The recovery/renewal implementation was first installed at SHA-256 `9821beb184476e4c8ad1c7f80d589f230ce51ec16dde75d1b9846ce4405b8456`. The current native string-type correction was independently reviewed, approved at exact revision `398ecf6676997c9e585605b4818944cacea17e07`, merged as `77eb1bc995066bca2a077f1210495e661d0eabbe` and installed with a byte comparison at SHA-256 `5ce06f78cc15a315963191a1211bb95066193a0c391cc8f547c4839a0a2f5b6e` before execution. The recorded intended map digest is `7e060e69fcd38e9943a6101f212892f1ff968708c39d32f0ddbe17486e180115`; require the same fresh digest before `recover-initial`. Its step 4 binding is already complete: candidate recovery, when needed, continues directly with step 5 `accept`, without changing the UI again. Recovery, acceptance and repeat import are complete on Athena; do not repeat them to recreate commissioning. For a future installation, proceed only to its separately approved repeat-import test after checking the accepted map.

If transfer, hashing or inspection fails, execute no further runner action. Restore only the public runner from the recorded `previous_revision` using the same stdin transfer and require its recorded hash before inspection. Original revision `28c09e0cd0d1b266f2de3fb857f6265d644f5e62` hashes to `3048b620eeea10d55fbcb7942f000547132ba52a6fd4c557c62b17e6ab019b09`; the earlier recovery runner hashes to `9821beb184476e4c8ad1c7f80d589f230ce51ec16dde75d1b9846ce4405b8456`. This code revert does not revert the live certificate, client or private state. Revision `28c09e0c` lacks `recover-initial`/`renew-now`; revisions `584da904`, `747076cc` and merge `2040f142` share the rejected boolean default flag. Do not enable automated imports with an uncommissioned replacement path during recovery. If only candidate state was published, its unchanged `accept` understands that state, but do not use a code revert as an instruction to proceed. Retain state and review the failure. Do not reinstall the entire tree or provision the token again.

## Commissioning

All commands below execute on Athena as root through `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py <command>`. They are manual steps in an approved operation, not an automatic post-merge hook.

1. **Capture the baseline.** Run `inspect`. Retain the sanitized certificate IDs/defaults/service map in #1094. Confirm original `ALK0dn` and all six original bindings, no object named `athena-dsm-acme`, and accessible management SSH. Record S3 container IDs/start times/restart counts, health and trusted public leaf fingerprint using read-only inspection. Confirm NAS system time, outbound HTTPS/DNS and normal public CA trust. Renewal's live checks connect to loopback with canonical SNI, avoiding dependence on the cluster's hostname answer.
2. **Issue once.** Run `issue`. This registers a separate Let's Encrypt account and creates/removes its own challenge TXT value with the existing token. Confirm only the canonical SAN, matching private key, trusted chain and sufficient validity. A successful issue does not mean DSM is serving it. On failure preserve the private state, identify any specific leftover challenge value, and clean up only that value after approval; never delete another client's TXT value. Do not force repeated production issuance. After the 120-second challenge TTL, query every authoritative nameserver with `dig +short NS kelch.io` and `dig +norecurse +short @<nameserver> _acme-challenge.athena.home.kelch.io TXT`. Confirm this run's value is absent. In a quiet commissioning window there should be no TXT answer; investigate any remaining value rather than deleting another client's data.
3. **Import a new object.** Run `import-new`. It must retain the original object, assignments and default before any UI change. Record the returned and re-read new certificate ID. If import response or postchecks fail, stop; inspect both DSM's inventory and the private state before retrying, because the API or an operator may have applied a change despite the failure. `import-new` can recover an existing uniquely named, nondefault, unassigned object only when the rest of the saved baseline is unchanged. If the exact intended final DSM/default transition is already complete and trusted but `candidate.json` was never published, review the complete map and its `current_sha256` from `inspect`, then run `recover-initial <reviewed-current_sha256>` using an explicitly reviewed and approved runner revision. This requires the saved initial baseline, exact permitted transition, unchanged map and issued leaf on both listeners; it publishes only the missing candidate, performs no issuance/import/binding change and does not mark acceptance or renewal success. Then use regular `accept`. Do not roll back working HTTPS merely to recreate the pre-binding checkpoint. Any other transition requires investigation or the initial rollback below; never edit protected JSON or bless an unexpected map.
4. **Bind only DSM.** In DSM Control Panel → Security → Certificate → Settings, assign `athena-dsm-acme` to DSM Desktop Service (`system/default`) and establish the intended default marker if required by DSM. Explicitly retain every unrelated service on `ALK0dn`. Do not replace the contents of `ALK0dn`, reset certificates or delete the original. Record the complete resulting map. Permit a short DSM web-session disruption and measure it; do not promise a zero-interruption import.
5. **Accept the baseline.** Run `accept`. Require exact single-name identity, normally trusted live leaves matching the issued certificate on both 443 and 5001, only the approved binding/default move, and retained original object. Recheck S3 health, certificate and container identity/start/restart metadata. If anything unrelated changed, stop before enabling scheduled import and review the effect.
6. **Exercise import and the no-renewal path.** Run `reimport` to apply the accepted bundle through the same guarded native import path used by renewal; verify both listeners and the unchanged map. Then run `run` twice. Both must verify the accepted mapping and live certificate, and write success only after all checks. A not-due client return is expected; it must not trigger a new forced order. `watchdog` must then pass. A same-certificate run may skip an unnecessary import. Neither a repeat import nor a not-due run proves a genuine renewal. Do not force an order in this step; the only forced commissioning order is the separately approved supervised test below, which is not unattended-renewal evidence.
7. **Verify ordinary access.** From a normal client, open `https://athena.home.kelch.io/`, observe its landing transition and verify the trusted `:5001` UI. Check both URLs with normal curl verification. Verify the management-address override below. Record TLS identity/expiry and the actual interruption. Only after acceptance, update Homepage's `href` to `https://athena.home.kelch.io:5001/` and commit the verified current-state documentation. Keep its existing `siteMonitor` at `https://10.32.20.5:5001` until the canonical URL is verified reachable from the Homepage pod; that check timed out on 2026-10-29, with the cause unverified. The Pi's central probe supplies normally trusted DSM TLS/expiry monitoring.

Before scheduling, confirm failed issuance/import can be diagnosed without publishing private logs. The selected central monitoring route is commissioned separately; email delivery is not a scheduling prerequisite.

### Supervised different-leaf renewal test

Completed once on 2026-10-29 as recorded below. This remains a reusable procedure; any further production order needs new explicit approval.

This is a separately approved commissioning action, not a daily job. After acceptance and same-leaf `reimport` pass, review the exact runner revision and current known-good leaf fingerprint with Matt. Approval must cover **one additional production certificate order**, any required temporary DNS-01 TXT creation/removal, same-ID import/reload, and, if included, a live rollback and return to the newer leaf. Keep the scheduled renewal task disabled or not yet installed throughout the test and rollback. Keep the management recovery connection available and remain within the coordinated NAS window. The single SAN and unchanged shared credential permissions still apply; private state stays protected. Never enable forced renewal in the daily task.

Run `renew-now <reviewed-current-good-leaf-sha256>` once. It requires that the accepted map, saved good bundle, issued bundle and both live listeners agree with that fingerprint before requesting a new certificate. It must obtain a different validated leaf, preserve the previous bundle, import into the same accepted certificate ID, preserve the complete service/default map and verify the new leaf on both listeners. Recheck S3 metadata, health and trusted leaf, and query authoritative TXT answers after the challenge TTL. Record both fingerprints, expiry, exact certificate ID and map, operation results, observed interruption and whether a fresh DNS challenge was actually observed. This demonstrates a new production order/download, stable install and different-leaf replacement; it does not prove unattended execution, key rotation or the client's normal renewal-due decision.

Let's Encrypt currently permits [authorization reuse for 30 days](https://letsencrypt.org/2025/12/02/from-90-to-45/). A test soon after today's issuance will probably reuse that authorization and create no TXT record. Count DNS-01 as unexercised unless a fresh challenge was independently observed, such as an authoritative TXT answer during the run or a Cloudflare audit entry. `--force` also bypasses the client's ARI fetch and due-time decision. Matt chose the supervised test on 2026-10-29 with those limits. A later real renewal after authorization expiry (approximately 2026-11-28) can exercise fresh DNS-01; do not create another commissioning order to fill that evidence gap. Fresh DNS-01 renewal, the normal due decision and unattended scheduling remain later acceptance gates.

After **any failed `renew-now`**, diagnose the failure and use ordinary `run`, never another forced attempt, to surface and apply any certificate already obtained. A killed client can have saved a newer certificate before updating `issued/`; the forced precheck cannot detect that narrow state. If the test fails, do not repeat a forced order. When a newer issued bundle is pending import, use ordinary `run` after diagnosing the failure; `renew-now` deliberately refuses that state. After successful renewal the old fingerprint also refuses another forced test. Review any further issuance separately. If a separately approved live rollback test is included, run `rollback` once, verify the prior trusted leaf and unchanged map, then run ordinary `run` to restore the newer issued leaf without another forced order. Verify both listeners and S3 again before scheduling. Stop and investigate any unexpected map, identity, trust or service effect.

## Scheduling, monitoring and expiry

Matt selected the existing **Pi blackbox → VictoriaMetrics/Grafana/Pushover** path for served-TLS health and expiry notifications. Do not configure DSM task email, create a disposable notification-test task or change DSM's existing mail configuration. Root owns the small central DSM probe and DSM-specific expiry lead time, preserving the existing 36-hour rule for short-lived cluster certificates. [PR #1113](https://github.com/kelchm/home-lab/pull/1113) prepares a separate 14-day warning held for one hour; its deployment and the Pi target are separately pending. Track actual rollout/verification in #1094; preparation is not evidence that the DSM target is active.

Create one **root / user-defined script / daily** task in DSM Task Scheduler, initially disabled. DSM uses `US/Eastern`; **00:17** is 04:17 UTC during daylight time and 05:17 UTC in standard time. Keep **Send run details by email** off. DSM requires account-password confirmation for saving a root task even without email; complete that directly in the UI without storing an administrator password in the runner.

| Task | Daily local time (`US/Eastern`) | Script |
|---|---|---|
| Athena ACME renewal | 00:17 | `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py run` |

Verify the exact name, root owner, daily time, disabled state, email off and command before saving. Run the disabled task once using DSM's Run action. Require successful task status, a fresh verified-success marker, unchanged accepted certificate map, normally trusted expected leaf and unchanged S3. Then enable only this task and record its next run time. The task must call ordinary `run`; never schedule `renew-now` or `--force`. A first automatic not-due run is separate from manual execution and from the first due unattended production renewal.

The runner keeps sanitized nonzero failure status for client/import/map/trust/expiry failures; detailed output remains private. No trailing successful shell command may hide failure. Central probes observe the served certificate and endpoint, not individual early renewal/import job failures. Matt chose that notification tradeoff; do not add mandatory job-status infrastructure or claim immediate per-job alert delivery. Renewal itself remains independent of Kubernetes, while central notification depends on the cluster and its existing alert path.

`watchdog` remains an operator diagnostic and passed its manual commissioning check. It verifies the live leaf and requires a fully successful run within three hours, so use it soon after a successful run; it is not a general 24-hour freshness check. No daily watchdog task is installed or required under the selected notification approach.

The daily runner lets the pinned client decide when renewal is due; it never requests daily forced renewals. Stable output is refreshed even on not-due runs. Removal matches either the raw TXT value or its single quoted form. A protected `dns-cleanup-pending.json` records a cleanup attempt until it succeeds; the parent retries that exact value before/after client execution and fails the task if cleanup still fails, because acme.sh itself ignores removal-hook failures. A killed process that never reached its cleanup hook can still leave a TXT record; a timeout already fails the task and requires the public-DNS inspection above. If issuance succeeded but import failed, the existing issued bundle is retried instead of requesting another certificate. The renewal and optional manual watchdog checks require more than **seven days** of validity. A failed client renewal/import returns nonzero immediately, but the selected central route does not forward that job status. Operator `rollback` and `reimport` permit a normally trusted leaf with more than **one day** remaining. These separate floors preserve a recovery window and avoid treating healthy 45-day certificates as failures before their ARI renewal time. [Let’s Encrypt's published changes](https://letsencrypt.org/2025/12/02/from-90-to-45/) reduce default-profile validity to 64 days on 2027-02-10 and 45 days on 2028-02-16; review the client's ARI behavior and alert margins when those changes or a different profile take effect.

Central served-TLS checks can detect an unreachable NAS or an approaching certificate expiry even if its scheduler stops, but do not diagnose the missed run itself. Their collection/alerting availability remains covered by the existing monitoring system. The [service-probe runbook](host-monitoring.md#service-probes) maintains the accepted DSM target and lead time; #1094 retains commissioning evidence. Do not claim cluster-independent notification or emit an unread `.prom` file: the current NAS exporter has no textfile collector enabled.

The first natural due unattended production renewal remains pending. Same-ID replacement by a different leaf and actual rollback passed the supervised test on 2026-10-29; the first automatic daily execution passed on 2026-10-30. These establish different parts of the path. Neither establishes fresh DNS-01 at renewal or the client's normal ARI/due-time selection. When a natural renewal occurs, record its new fingerprint/expiry, successful same-ID import, unchanged bindings, normally trusted live leaves and whether a fresh challenge occurred in #1094. Do not force another order to fill this evidence gap. Additional supervised issuance requires a separate owner decision. WAN-outage and WAN-recovery behavior was not tested; issuance/renewal requires outbound DNS/HTTPS to the CA and Cloudflare.

SIGTERM, SIGHUP and SIGINT stop and reap the current child process group before releasing the operation lock. SIGKILL cannot execute cleanup; after a forced kill, confirm no ACME client/helper remains before starting another operation. Archive and pointer publication synchronize their containing directories before reporting success, preserving the recovery ordering across a power loss.

## Verified certificate commissioning on 2026-10-29

The approved string-type correction passed the previously failing native import gate. All operations retained certificate ID `N2RhLk`, description `athena-dsm-acme`, its DSM-only/default assignment and the five unrelated bindings on `ALK0dn`. The complete accepted map digest remained `7e060e69fcd38e9943a6101f212892f1ff968708c39d32f0ddbe17486e180115`.

| Completed operation | UTC completion | Observed result |
|---|---|---|
| Same-ID `reimport` | 17:18:03 | Prior trusted leaf retained; 28 availability samples, zero failures |
| One supervised `renew-now` | 17:18:42 | Different trusted production leaf imported; 78 samples, zero failures |
| `rollback` | 17:19:27 | Prior trusted leaf restored; 38 samples, zero failures |
| Ordinary unforced `run` | 17:19:53 | Newer issued leaf restored without another forced order |

The current served/good/issued leaf is SHA-256 `b237b6d14e8cae3502fa3ea9fafcbae71f8c8caafd721a1d1fca3d631b4e8404`, issued by Let's Encrypt YE2, expiring **2027-01-27 at 16:20:03 UTC**. The protected previous bundle is SHA-256 `770b95d7a00b871fff07b344161ae44fb32f47795467e7e6dcc578cb4b1821e4`, YE1, expiring 2027-01-27 at 14:34:28 UTC. Both contain only `athena.home.kelch.io`. A comparison of the two public certificates confirms the same public key; the test establishes certificate replacement and recovery, not key rotation.

Normal trust and the expected leaf passed on 443 and 5001 at both `10.32.10.5` and `10.32.20.5` after each transition. The 17:58:40 final native-commissioning checks also confirmed the accepted map, protected state, absent cleanup-pending marker and unchanged S3 trusted leaf; S3 container IDs/start times/restarts/health matched the pre-test baseline. A post-order query of both authoritative nameservers found no leftover challenge TXT value. No fresh challenge was independently observed during the order; same-day authorization reuse is likely but unconfirmed, so fresh DNS-01 renewal remains unverified. No temporary administrator, OTP change, direct generated-tree write, explicit restart, S3 deployment or database action was used.

### Native scheduler acceptance

Task **`7` / Athena ACME renewal** was saved disabled as root, daily at **00:17 US/Eastern**, with email off and exactly `/usr/bin/python3 /volume1/@athena-acme/athena-acme.py run`. Matt completed DSM's root-task password confirmation directly in the UI. A manual DSM Run on the disabled task started at **17:56:25 UTC** and stopped at **17:56:27 UTC**, reporting **Normal (0)**. The protected verified-success marker also records 17:56:27 and the expected renewed leaf.

Only that task was then enabled. Native read-back at **17:58:29 UTC** verified its root owner, enabled state, daily 00:17 schedule, exact command, email disabled and empty recipient. The three pre-existing tasks (IDs `2`, `5`, `6`) exactly matched their pre-change metadata. The final 17:58:40 certificate/map/S3 checks passed; the owned recovery SSH connection was closed and NAS/browser ownership returned before the 18:00 cutoff. No task or certificate operation remained active.

The first automatic occurrence started **2026-10-30 at 00:17:01 EDT / 04:17:01 UTC** and stopped at **00:17:07 EDT / 04:17:07 UTC**. Native `synoschedtask --get id=7` reports **Success**. A read-only query of the native scheduler status database confirms both timestamps for task `7`; its internal status code `3` is the success state, not process exit code 3. The protected verified-success marker records **04:17:06 UTC** with the expected `b237b6d1…` leaf, within that occurrence. Task `7` remained root/enabled/daily 00:17 with the exact ordinary `run` command and next trigger 2026-10-31 00:17 Eastern. This was a metadata check; no runner command was invoked to manufacture execution evidence.

The **2026-10-30 22:06:55 UTC** read-only check confirmed the unchanged complete accepted/current map, certificate ID/default/bindings, installed runner hash, good/issued/previous fingerprints and absent cleanup-pending marker. All four DSM listeners passed normal trust with the expected current leaf. S3 container IDs/images/start times/restart counts/health and its normally trusted leaf exactly matched the 2026-10-29 baseline. The unchanged leaf and fresh success marker establish a successful scheduled execution; no natural due renewal or fresh DNS challenge is claimed.

The original email-test dialog was cancelled without saving a task or sending a message. Matt selected central monitoring instead; no watchdog or disposable email task was created.

### Central monitoring acceptance

[rpi-nixos #25](https://github.com/kelchm/rpi-nixos/pull/25) merged as `e0fe7cf1` and passed test and persistent generation-13 activation on `ups-compute-rack` without a reboot. The Pi's canonical DSM 5001 probe passed normal trust/HTTP 200 with the expected renewed leaf and fresh success/up/SSL samples; all nine prior probes remained healthy. [Home-lab #1113](https://github.com/kelchm/home-lab/pull/1113) merged as `665e5476`, was applied by Flux and loaded the DSM-only **less than 14 days, held for one hour** warning. The generic **36-hour** rule remains unchanged for existing targets and excludes only DSM.

The approved synthetic DSM warning fired through `pushover-warning` at **2026-10-29 18:29 UTC**, and Matt confirmed delivery. Pushover failure counters remained zero. The temporary rule was deleted with its exact UID at 18:30:17; its runtime group and synthetic alert were absent at the 18:33:51 final check. The real rules were healthy/inactive, with ten probes and eleven scrape series healthy. The [service-probe runbook](host-monitoring.md#service-probes) maintains this coverage. Central notifications depend on the cluster and observe served TLS/availability/expiry rather than individual early renewal/import failures. This accepted limit does not require another notification system.

## A legitimate later DSM binding change

The [#1131](https://github.com/kelchm/home-lab/issues/1131) guard correction is prepared in the repository but has not been installed on Athena. Until an approved runner update is installed, the existing runner still freezes the complete accepted map and requires retained `ALK0dn`. The behavior below describes the corrected runner; merging does not deploy it.

Normal renewal requires the recorded, uniquely named `athena-dsm-acme` certificate, its accepted metadata and service assignments, its default marker and its DSM `system/default` binding. A broken managed certificate still stops the operation. Adding, removing, replacing, rebinding or marking an unrelated certificate broken does not by itself block renewal or require rebaseline. The original `ALK0dn` ID remains protected from import and is part of historical commissioning checks, but its continued presence is no longer a standing renewal or rebaseline requirement. Its 2027-01-03 expiry is not a demonstrated renewal-failure date.

Each import compares the complete current binding map before and after replacement, including unrelated objects. It must leave that map unchanged; unrelated changes between separate runs are allowed. The existing private-state format, certificate bundles, rollback pointers, client configuration and scheduler remain unchanged.

Use `review-bindings` if investigating drift or deliberately changing the managed certificate's assignments. It shows the saved/current sanitized maps and `current_sha256`; the saved map may legitimately contain older unrelated objects. Do not edit `accepted.json` by hand. For a reviewed binding change, disable the renewal task, verify the intended assignments, then run `rebaseline <reviewed-current_sha256>`. Rebaseline requires the same uniquely named certificate ID, DSM `system/default` assignment and default marker, an unchanged current map during the operation, and the last known-good trusted leaf on both listeners. It updates only the private accepted map, without issuing/importing or advancing the renewal success marker. Run ordinary `run` and `watchdog` afterward before re-enabling scheduling.

If a renewal import unexpectedly changes bindings, diagnose and restore the intended bindings in DSM's UI before retrying. Do not use rebaseline to bless an unexpected import side effect; it requires the previously verified good leaf. The commissioning and historical rollback references to `ALK0dn` do not authorize removing it or migrating its unrelated consumers.

## Recovery and rollback

Retain SSH at **10.32.20.5**. A DNS-independent, fully verified client check keeps the hostname while overriding only the connection address:

```sh
curl --resolve athena.home.kelch.io:443:10.32.20.5 https://athena.home.kelch.io/
curl --resolve athena.home.kelch.io:5001:10.32.20.5 https://athena.home.kelch.io:5001/
```

An ordinary raw-IP browser URL still has a name mismatch. For trusted browser recovery, temporarily map the canonical name to the management address on that client. Preserve the existing IP fallback as an emergency route, with that limitation explicit. No cluster service is needed by the native renewal client or its loopback verification.

For initial activation rollback, disable the renewal task. In DSM Certificate Settings restore DSM Desktop Service and the default marker to **`ALK0dn`**, checking the saved original map. This returns the known trust/name warning while recovering the original path. Retain the new object and private client state for diagnosis. Restore Homepage's previous link only if necessary. Do not delete/revoke certificates, reset DSM certificates, restart services or modify S3 state.

For a later bad renewal, disable the renewal task, then use `rollback` to import the protected last-known-good bundle into the same recorded `athena-dsm-acme` object through DSM's API. It must preserve the accepted map and verify the previous trusted leaf on both listeners. Do not roll back ACME account/renewal metadata or revoke either certificate. Investigate the newer candidate before re-enabling `run`, which would otherwise try to apply it again. If no valid previous trusted bundle exists, restore the original assignment through DSM and accept the temporary warning. A failed rollback needs operator investigation, not a reboot or generated-file rewrite.

Metadata naming rollback changes only the Cloudflare label and 1Password title back to `athena-s3-storage-acme`. The stable item-ID reference and both runtime token values remain valid. Never revoke the shared token as cleanup for a DSM failure.

## Sources and checks

- [Let's Encrypt DNS-01](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge): private web servers can prove domain control through TXT records.
- [Pinned acme.sh core](https://github.com/acmesh-official/acme.sh/blob/807da6498377ee5e0cf43a78091f46f12dc59a89/acme.sh): exact issuance, ARI and stable-output behavior. The local [DNS adapter](../../scripts/synology/dns_athena.sh) delegates authenticated requests to the native Python runner using [Cloudflare zone listing](https://developers.cloudflare.com/api/resources/zones/methods/list/) and [DNS records](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/).
- [acme.sh installation guidance](https://github.com/acmesh-official/acme.sh/tree/807da6498377ee5e0cf43a78091f46f12dc59a89#3-install-the-cert-to-apachenginx-etc): use stable installed outputs instead of depending on internal certificate files.
- [Tailscale native Synology import implementation](https://github.com/tailscale/tailscale/blob/bd2a2d53d3a4d632c9fae7c1b6426c3b5ef34010/cmd/tailscale/cli/configure-synology-cert.go): Go `%q` string-argument quoting, root execution, protected staging, omit ID for create and use ID for replacement; it does not send `as_default`. Its tailnet issuance and selection logic are not used here.
- [Synology certificate settings](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/connection_certificate?version=7) and [Task Scheduler](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_taskscheduler?version=7): supported operator UI for binding and failure-email configuration.

Run `python3 scripts/ci/test-athena-acme.py` and ShellCheck on the preparation script before publishing changes. Offline mocks establish guard behavior, not successful DNS issuance, DSM import, reload scope, mail delivery or actual renewal. Keep those observations distinct in #1094.
