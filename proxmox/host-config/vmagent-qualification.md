# Native PVE local vmagent qualification

This October 3, 2026 follow-on to [qualification.md](qualification.md) and [#650](https://github.com/kelchm/home-lab/issues/650) qualifies the bounded native push engine on the authorized disposable Debian 13 VM 990, `monitoring-eval-990`, `10.32.21.239`. Production push is prepared, not enrolled: all three checked-in node objects keep vmagent absent, exporter enrollment stays enabled only for node 1, and nodes 2/3 remain disabled.

## Verified preparation and guest acceptance

The harmless real Git/local-Ansible harness passed with upstream Ansible core 2.19.11. It retains the corrected channel's duplicate rejection and recovery checks and adds backward compatibility for old node objects, strict present/absent state, rejection of fetched host identity/URL/flags, controller-injected identity, independent push removal, and read-only drift reporting. The push slice retains the channel's scoped transient plays, durable cleanup reference, dirty restart and real-apply daemon reload.

Explicit guest bootstrap downloaded official native Linux amd64 vmagent v1.152.0 and verified both the official manifest and engine pins: archive SHA-256 `8eee4a98ff1665c60682475e8a8b292b8d718b63a2f023124384dd2f6a220c79`, binary SHA-256 `a9fa98b7447b94f6bcf93b1c43fd3192b8f0cb2e8c54940af144692274bf4711`. The binary reported `vmagent-20260911-125958-tags-v1.152.0-0-g540b91da03`, root:root mode 0555; its checksum is root-owned mode 0444. Debian reported zero upgrades, newly installed packages or removals because the pinned Git/Ansible engine already existed.

| Check | Verified result |
|---|---|
| Runtime identity and listener | Actual `/proc` UID/GID fields were 65534 throughout; listener was only `127.0.0.1:8429` |
| Native production unit | Exact fixed HTTPS URL, bearerTokenFile, one queue, `maxDiskUsagePerURL=1GiB`, `memory.allowedBytes=64MiB`, and engine binary/config paths appeared in the live command |
| Resource/security settings | systemd reported MemoryMax=268435456 bytes, CPU quota 250 ms/second, ProtectSystem=strict, and stop timeout 30 seconds |
| TLS/authentication | The unmodified production unit successfully wrote through verified TLS to the loopback fixture using its credential file |
| Check mode | A deliberately stopped collector was reported as drift; check did not start it |
| No-op and code delivery | Same payload and an unrelated fetched binary change left the installed binary checksum and vmagent PID unchanged |
| Invalid native config | A real unsupported native field failed strict dry-run; applied SHA, prior config bytes and running PID were preserved |
| Bad or missing credentials | Malformed and temporarily absent tokens failed preflight; applied SHA, prior config and running PID were preserved |
| Same-SHA recovery | Restoring valid template/token converged the same desired SHA, cleared dirty and reactivated vmagent even though its published artifacts were unchanged |
| Removal and Git revert | Explicit absent removed owned files, retained credentials/queue and left node exporter active; a real Git revert rebuilt vmagent |
| Historical replay | The production unit queued through 503 responses, gracefully stopped, restarted, replayed the outage samples with their original timestamps and integer value, and drained its queued-data metric |
| Atomic token rotation | Six 401 responses caused queued retries; replacing the token inode restored authenticated writes and drained the queue without a PID change |
| Drift and final removal | Check and unchanged sync reported manual config drift without repair; explicit absent then removed the collector and retained credentials/queue |
| Drift-deleted unit removal | After unlink plus daemon-reload, `LoadState=not-found` coexisted with an active PID. Check reported drift=1 without stopping/repairing it. Explicit absent stopped that exact loaded unit and removed its owned enablement/config while preserving exporter/token/queue |

The replay test observed 208,238 queued bytes before graceful stop and 247,182 bytes in queue files afterward. The synthetic sample value was `1791067901`. The receiver subsequently stored its four original scrape timestamps: 2026-10-03T22:51:50.612Z, 2026-10-03T22:52:05.612Z, 2026-10-03T22:52:20.612Z, 2026-10-03T22:52:35.612Z. Samples kept `instance=pve-sbx-1`, `host=pve-sbx-1`, `platform=proxmox`, `collector=pve-host-vmagent`, and `job=pve-node`, with no cluster label. The fixture changed the sample phase after stopping vmagent, so these outage samples could only come from replayed data. The assertion uses an exactly representable integer; arbitrary backend float-render equality is not assumed.

After drain, `vm_persistentqueue_bytes_pending` was zero while queue files still occupied 247,186 bytes, including a 247,120-byte segment and a 66-byte metadata file. Drain means no logical pending data, not removal of segment/lock/metadata files. The token-rotation test had 61,782 queued bytes, changed its inode from 139559 to 139566, and recovered from 19 to 22 accepted writes on the same PID.

## Bootstrap and download regressions

A bounded guest probe killed a real manual controller while a detached, TERM-resistant harmless worker remained in its recorded transient play. Explicit bootstrap collected that exact unit and cleared the durable reference before its first download/package/engine step; no delayed completion marker appeared after the worker's original deadline. Bootstrap retained the applied SHA and published paused/dirty=1. Accepted resume on the unchanged payload activated a harmless engine-generated exporter environment marker with a new PID, cleared dirty, and the next unchanged tick preserved the PID. The fixture template, exporter unit, repository config and timer state were restored.

The final bootstrap separately proved that it publishes dirty=1 and retains applied history before its first download/package/engine step. The final private-publication helper's check compared bytes and metadata without creating a candidate or calling a deliberately failing validator. SIGTERM during apply removed its hidden non-unit candidate and exact validator process; the next apply swept an owned stale candidate. Invalid native validation preserved the original `/dev/null` mask inode, kept stdout reserved for the changed contract and exposed the validator's reason on stderr. Accepted unchanged-SHA recovery replaced the mask and cleared dirty; the following tick preserved the PID and attempt timestamp. An occupied, inactive alternate unit under `/run/systemd/system` was refused before any download/package/engine step. Forced bootstrap unit verification from a checkout containing spaces and `&` also passed.

A final harmless probe launched this installed engine through `pve-hostcfg.service` and stopped the outer service mid-apply. Its detached worker ignored SIGTERM. `systemctl stop` returned after 5.344 seconds with the exact transient play collected, worker gone and durable reference cleared. A subsequent check acquired the lock with dirty retained, and no completion marker appeared past the worker's original 30-second deadline. The original configuration and paused timer were restored.

The download helper uses an absolute per-attempt SIGALRM deadline covering URL open, response headers and body reads. A harmless loopback probe extracted the real download function and shortened that deadline to 0.2 seconds while suppressing retry backoff. Slow headers and continuous body trickle each timed out after three attempts in about 0.61 seconds, removed partial files and cancelled the alarm. Size rejection, a successful response and timer cancellation also passed. Actual official download and binary acceptance were separately verified by the guest bootstrap.

## Fixture scope and cleanup

The engine and production unit have no fake-upstream override. The guest test temporarily mapped `metrics-ingest.home.kelch.io` to loopback in `/etc/hosts`, installed a one-day TLS fixture certificate with that DNS SAN, and refreshed Debian's trust store. It provisioned only synthetic credentials at `/etc/pve-host-vmagent/token`, root:65534 mode 0640 in a root:65534 mode 0750 directory. No production token, age key or secret from Git was used. The receiver and checksum-verified native VictoriaMetrics v1.152.0 store ran in named fixture services, with a bare fixture Git source and evidence under `/tmp/pve-push-eval`.

Cleanup restored the original hosts bytes, removed the temporary trust anchor and refreshed the trust store, restored the original bootstrap repository config and exporter unit/state, stopped and removed both fixture services, removed the fixture textfile, and enabled the paused controller timer. At lease release, vmagent was stopped with its owned unit, enablement symlink and scrape config removed. The pinned binary, synthetic token and queue evidence were retained for inspection. No fixture service or worker remained active.

The final inventory verified the installed engine against the reviewed source, absence of fixture trust in the effective CA store, absence of temporary trust links and private unit candidates, no transient plays or fixture processes, original config/exporter unit bytes, loopback exporter and a paused active timer. Ordinary trust refresh had left a dangling fixture PEM link and its exact hash link; these were removed and refresh plus inventory repeated successfully. The original exporter fixture's pre-existing drift was retained (`dirty=0`, `drift=1`); no unrelated repair was performed.

The parent subsequently destroyed VM 990 and all three owned volumes, removed its snippet and temporary hypervisor files, and restored the backup exclusion to `201,103`. All five persistent guests remained running, template 9000 remained stopped and intact, and cluster quorum retained three votes. Guest credentials, queue and fixture archives disappeared with the volumes; the workstation qualification evidence remains. No production enrollment was performed.

## Remaining live gates and limits

Production canary enrollment still requires actual PVE package repository/collector acceptance, protected per-host credential provisioning, production TLS/authenticated ingest acceptance with the exact labels, and alert delivery. Nodes 2/3 remain disabled until that acceptance and separate enrollment approval. This follow-on performed no production hypervisor package/service changes, new guest reboot, production credential use, cluster deployment, network/firewall change or production rollout.

No queue-fill saturation, power loss or newest in-memory-tail loss test was performed on the hardened guest unit. The native setting caps queued data for its single destination at 1 GiB; filesystem/metadata overhead is separate and oldest data is dropped when the native limit is reached. The observed graceful restart replay does not establish zero loss or power-loss durability. UID/GID 65534 is the existing Debian nobody/nogroup identity, not a separately isolated service account.
