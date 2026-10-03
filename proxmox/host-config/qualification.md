# Disposable Debian qualification record

This record covers the October 3, 2026 qualification for [#696](https://github.com/kelchm/home-lab/issues/696) and the delivery choice in [#650](https://github.com/kelchm/home-lab/issues/650). The authorized target is only VM 990, `monitoring-eval-990`, at `10.32.21.239` on `pve-sbx-2`. No production hypervisor enrollment or mutation is part of these results.

## Observed baseline and completed acceptance

The guest is Debian 13.7, Python 3, systemd `257.13-1~deb13u1`, with QGA and existing operator sudo access. Git, Ansible, and node exporter were absent initially. Debian's actual available and installed package versions matched Git `1:2.47.3-0+deb13u1`, Ansible core `2.19.11-0+deb13u1`, and node exporter `1.9.0-1+b4`. Bootstrap installed Git/Ansible and required dependencies with zero upgrades/removals. No Galaxy packages, one-off mise installs, authentication secrets, accounts, hypervisor backup jobs, or networking were changed.

The initial real Git/local-Ansible fixture harness passed on the workstation with Ansible core 2.21.3 and on the guest with the pinned Debian Ansible core 2.19.11. It verified relevant-only applies, no-op/unrelated/dirty-worktree behavior, missing Git and bounded hung Git transport, invalid same-SHA retries, partial mutation then same-SHA recovery, reverting a failed payload to the applied tree, nonblocking lock, pause persistence across controller processes, rc=0/changed>0 drift without repair, explicit removal, truthful revisions/results, and disabled enrollment. The final harness also exercises durable pause while locked and preservation of the applied Git tree after a force push and garbage collection. The revised harmless fixture additionally passed on the workstation with upstream Ansible core 2.19.11, including duplicate-collector rejection.

The final real package/systemd guest pass completed these checks:

| Check | Observed result |
|---|---|
| Initial pause | Desired fetched without an apply or exporter installation |
| Interrupted first install | Injected validator failure after the real package transaction left applied unset, dirty=1, attempt_success=0, and the unit masked/inactive |
| Default listener containment (initial engine) | Existing policy-rc.d restored byte-for-byte; port watcher saw no non-loopback listener; service journal contained no starts before validated activation |
| Retry without a new commit | Clearing the injected fault allowed the identical SHA to activate successfully, clear dirty, and advance applied |
| Exporter activation | Only `127.0.0.1:9100`; real HTTP metrics included `node_uname_info` and controller textfile status |
| Unrelated engine/docs commit | Desired advanced; applied, exporter process start time, and installed controller hash did not change |
| Invalid flags | Two retries failed validation; existing valid unit bytes and running service survived; applied remained the last successful revision |
| Git outage | Unavailable real bare remote failed within the finite bound; exporter/metrics/applied state remained available |
| Revert | Actual Git revert commits restored the previous valid unit bytes and fully converged |
| Drift | Manual unit modification produced changed>0/drift=1 on check and unchanged sync; bytes were not repaired |
| Removal | Exporter stopped and package/owned unit were removed; operator-owned textfile and controller status remained; check was clean for absent state |
| Rebuild | Explicit present payload installed and activated the loopback exporter again |
| Timer and pause | Calendar timer enabled with next/last trigger observations; a new pending payload remained unapplied while paused |

Ansible may replace a mask symlink with an empty unit file while failing template validation. Both are masked in systemd; the qualification therefore checks systemd's actual `LoadState=masked`, rather than requiring one filesystem representation. The existing valid regular unit was preserved during invalid-flag validation. The interrupted-install contract is service containment plus dirty/retry status, not globally atomic rollback.

## Initial engine reboot acceptance

The initial explicitly installed engine passed the Git/local-Ansible harness with Debian's pinned core 2.19.11 and repeated all package/systemd checks. An actual reboot changed the boot ID from `530dc238-e0be-41d9-9658-204f707a6a9f` to `db7768d1-886e-4f31-957a-bdd849c031ab`. The exporter remained active on `127.0.0.1:9100`, the timer remained enabled and active, and pause survived. Applied stayed at `09400dd4a60cd63b4d67c5bcac6732977e187850`; pending desired `edcd143526d4118d3ff953abdc92236348416a9c` never applied. The final status reported dirty=0, drift=0, paused=1, service_active=1, attempt_success=1 and fetch_success=1 after the source was restored.

Debian cleared the fixture's `/tmp` Git source during reboot. Before restoration, the controller reported fetch failure while checking its retained applied tree; the exporter continued running. The source and saved test evidence were restored to complete the automated post-reboot assertions. No engine code change was needed. The parent independently checked the post-reboot boot ID, service, timer, listener and revision/pause status.

The guest harness watched listener activation and observed zero non-loopback port-9100 listeners. Its interrupted first-install check confirmed systemd masked/inactive state and no starts before validation; subsequent invalid flags preserved the existing regular unit. These observations establish this tested failure path, not every possible package-maintainer interruption or power-loss outcome.

## Review hardening

Review reproduced a timeout escaping the original subprocess-group boundary under Ansible 2.19.11: workers call `setsid`, so the old controller could return failure, check drift, and release its lock while a module was still running. The revised controller uses a uniquely named transient systemd service for every syntax/check/apply, with `RuntimeMaxSec`, control-group termination, and a five-second stop grace. An abnormal wrapper timeout stops that exact unit before reading its remaining output; cleanup waits for collection before drift or lock release. The locally recorded unit name also permits the next lock holder to settle a play left by a forcibly killed manual controller. Git still supplies only immutable data; no fetched executable engine code was added.

Read-only `sudo apt-get -s --no-install-recommends --no-remove install prometheus-node-exporter=1.9.0-1+b4` on `10.32.20.21`, `.22`, and `.23` returned the same result on every PVE host: `0 upgraded, 1 newly installed, 0 to remove and 101 not upgraded`. The sole `Inst`/`Conf` entry was `prometheus-node-exporter (1.9.0-1+b4 Debian:13.7/stable [amd64])`; no new dependencies were required. Those commands wrote no packages or units. On that evaluated package state, the existing default-unit mask contains first installation without a global policy override. The revised playbook drops `policy_rc_d` entirely; apply/check/interruption never writes the operator's `/usr/sbin/policy-rc.d`.

Previously dirty state is captured before a new attempt and passed to the root-owned playbook as `force_restart`. Apply reloads systemd, and a dirty retry restarts the exporter even when a validated template was already published unchanged before interruption. Clean unchanged successful revisions still perform no apply, and ordinary drift is reported without repair. Duplicate collectors are rejected by the data schema before template validation/publication, closing the `--version` parser shortcut for repeated boolean flags.

The scoped Debian Ansible 2.19.11/systemd guest suite passed without another reboot. Runtime expiry returned the failed tick in 8.198 seconds; an abnormal six-second wrapper timeout against a 60-second unit returned failure in 7.931 seconds. Both started the real detached worker, collected its exact transient unit before drift/lock release, and saw no durable completion marker after the worker's original 23-second deadline. Manual SIGTERM cleared the play before exit; SIGKILL left the exact play recorded, and the next lock holder stopped it before check. An eight-second timeout after validated unit publication left the old PID/flags active with dirty=1 and applied unchanged; identical-SHA retry preserved the new unit bytes while changing live PID from 22011 to 27298 and activating `--collector.cpu`. Duplicate schema rejection preserved the existing unit/PID. A remove/reinstall with an existing exit-0 policy and injected validator failure left the unit masked/inactive; same-SHA retry activated loopback-only service. The policy bytes/mode stayed unchanged throughout apply, check, and interruption, and the listener watcher observed no non-loopback activation. Teardown restored the original exporter unit and enabled the timer with the durable pause retained.

A focused real-Ansible probe of the final cleanup path additionally used a detached shell worker that ignored SIGTERM. A four-second abnormal wrapper timeout returned failure after 9.150 seconds, including the five-second stop grace and eventual cgroup SIGKILL. The exact transient unit was collected before the probe released its lock; no completion marker appeared beyond the original 23-second worker deadline. Cleanup now issues one stop request, then polls collection instead of repeatedly submitting stop jobs. The TERM-resistant fixture is included in `tests/recovery.py` for subsequent full guest runs. No transient play units or wrappers remained after qualification, and the temporary seeded policy was removed to restore the initial guest state. The installed engine/configuration were not replaced by this scratch-snapshot qualification.

## Scope and limits

This qualifies package/systemd/controller behavior on a disposable Debian guest. It does not qualify actual PVE collector coverage, package installation/unit activation on PVE, production canary operation, vmagent buffering/push, off-host PVE API collection, or monitoring alert delivery. Those remain separately bounded slices. Status publication and a running unit are not proof that every host collector is healthy. Reversion and apply are not transactional. An interrupted apt/dpkg process may need deliberate recovery.

Leave VM 990 paused with the exporter loopback-only and the native timer enabled. Parent work owns removal of the guest and restoration of its temporary backup exclusion; implementation qualification does not close those outstanding operational steps. No production nodes were enrolled. The Git/local-Ansible fixture is also wired into repository validation; package/systemd and reboot acceptance remain an explicitly guarded guest operation.
