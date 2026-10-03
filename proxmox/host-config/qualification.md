# Disposable Debian qualification record

This record covers the October 3, 2026 qualification for [#696](https://github.com/kelchm/home-lab/issues/696) and the delivery choice in [#650](https://github.com/kelchm/home-lab/issues/650). The authorized target is only VM 990, `monitoring-eval-990`, at `10.32.21.239` on `pve-sbx-2`. No production hypervisor enrollment or mutation is part of these results.

## Observed baseline and completed acceptance

The guest is Debian 13.7, Python 3, systemd `257.13-1~deb13u1`, with QGA and existing operator sudo access. Git, Ansible, and node exporter were absent initially. Debian's actual available and installed package versions matched Git `1:2.47.3-0+deb13u1`, Ansible core `2.19.11-0+deb13u1`, and node exporter `1.9.0-1+b4`. Bootstrap installed Git/Ansible and required dependencies with zero upgrades/removals. No Galaxy packages, one-off mise installs, authentication secrets, accounts, hypervisor backup jobs, or networking were changed.

The real Git/local-Ansible fixture harness passed on the workstation with Ansible core 2.21.3 and on the guest with the pinned Debian Ansible core 2.19.11. It verified relevant-only applies, no-op/unrelated/dirty-worktree behavior, missing Git and bounded hung Git transport, invalid same-SHA retries, partial mutation then same-SHA recovery, reverting a failed payload to the applied tree, nonblocking lock, pause persistence across controller processes, rc=0/changed>0 drift without repair, explicit removal, truthful revisions/results, and disabled enrollment. The final harness also exercises durable pause while locked and preservation of the applied Git tree after a force push and garbage collection.

The final real package/systemd guest pass completed these checks:

| Check | Observed result |
|---|---|
| Initial pause | Desired fetched without an apply or exporter installation |
| Interrupted first install | Injected validator failure after the real package transaction left applied unset, dirty=1, attempt_success=0, and the unit masked/inactive |
| Default listener containment | Existing policy-rc.d restored byte-for-byte; port watcher saw no non-loopback listener; service journal contained no starts before validated activation |
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

## Reboot acceptance

The final explicitly installed engine passed the Git/local-Ansible harness with Debian's pinned core 2.19.11 and repeated all package/systemd checks. An actual reboot changed the boot ID from `530dc238-e0be-41d9-9658-204f707a6a9f` to `db7768d1-886e-4f31-957a-bdd849c031ab`. The exporter remained active on `127.0.0.1:9100`, the timer remained enabled and active, and pause survived. Applied stayed at `09400dd4a60cd63b4d67c5bcac6732977e187850`; pending desired `edcd143526d4118d3ff953abdc92236348416a9c` never applied. The final status reported dirty=0, drift=0, paused=1, service_active=1, attempt_success=1 and fetch_success=1 after the source was restored.

Debian cleared the fixture's `/tmp` Git source during reboot. Before restoration, the controller reported fetch failure while checking its retained applied tree; the exporter continued running. The source and saved test evidence were restored to complete the automated post-reboot assertions. No engine code change was needed. The parent independently checked the post-reboot boot ID, service, timer, listener and revision/pause status.

The guest harness watched listener activation and observed zero non-loopback port-9100 listeners. Its interrupted first-install check confirmed systemd masked/inactive state and no starts before validation; subsequent invalid flags preserved the existing regular unit. These observations establish this tested failure path, not every possible package-maintainer interruption or power-loss outcome.

## Scope and limits

This qualifies package/systemd/controller behavior on a disposable Debian guest. It does not qualify actual PVE collector coverage, PVE package-repository differences, production canary operation, vmagent buffering/push, off-host PVE API collection, or monitoring alert delivery. Those remain separately bounded slices. Status publication and a running unit are not proof that every host collector is healthy. Reversion and apply are not transactional. An interrupted apt/dpkg process may need deliberate recovery, including checking the temporary policy file before proceeding.

Leave VM 990 paused with the exporter loopback-only and the native timer enabled. Parent work owns removal of the guest and restoration of its temporary backup exclusion; implementation qualification does not close those outstanding operational steps. No production nodes were enrolled. The Git/local-Ansible fixture is also wired into repository validation; package/systemd and reboot acceptance remain an explicitly guarded guest operation.
