# PVE host-local configuration

This is the bounded implementation for [#696](https://github.com/kelchm/home-lab/issues/696), following the delivery decision in [#650](https://github.com/kelchm/home-lab/issues/650). Production enrollment remains a separate manual canary rollout. No production host has been enrolled by this implementation.

A native systemd timer fetches a public/read-only Git branch every three minutes, with up to 30 seconds of randomized delay and persistent missed-run handling. A root-owned, explicitly installed controller runs the explicitly installed Debian Ansible engine against localhost. Git delivers only `payload/nodes.json`; fetched playbooks, plugins, inventory, `ansible.cfg`, shell scripts, engine changes, and documentation are never executed or installed. JSON is read from an immutable commit with `git show`, without checking out the repository. Only changes to the `proxmox/host-config/payload` tree trigger convergence. An unchanged successful payload is a no-op, including unrelated commits. A failed or interrupted apply retries on the same commit; a persistent dirty marker also forces recovery when a later revert restores the last applied tree.

## Ownership and dependencies

| Owner | Exact state |
|---|---|
| Explicit bootstrap/engine upgrade | Git `1:2.47.3-0+deb13u1`, Ansible core `2.19.11-0+deb13u1` and their required Debian dependencies; `/etc/pve-hostcfg/config.json`; `/usr/local/lib/pve-hostcfg/{controller.py,validate-unit,publish-unit,ansible.cfg,node-exporter.yml,node-exporter.service.j2}`; `/etc/systemd/system/pve-hostcfg.{service,timer}` and the timer enablement symlink |
| Controller | `/var/lib/pve-hostcfg/{repo.git,tmp,lock,paused,status.json,vars.json}`; `/var/lib/pve-hostcfg/textfile/pve-hostcfg.prom`; state/textfile directories |
| Initial payload | `prometheus-node-exporter=1.9.0-1+b4` and required Debian dependencies; `/etc/systemd/system/prometheus-node-exporter.service` and its enablement symlink; private `/etc/systemd/system/.pve-hostcfg-node-*.tmp` candidates during apply validation |
| Outside ownership | All other textfiles, preexisting exporter deployments, credentials, users, general packages/OS upgrades, kernels, reboot policy, networking, storage, Corosync, `/etc/pve`, and guest lifecycle |

The initial exporter listens only on `127.0.0.1:9100`, exposes ordinary host metrics plus the textfile collector, and optionally enables named built-in collectors. It runs as root for host read access with an empty capability bounding set, no new privileges, a read-only system/home, and kernel/control-group protections. It has no API token. A local vmagent and its credentials/buffering/push configuration are a later slice; there is no remote scraper route to this loopback endpoint yet.

The engine must already have Python 3 and Debian `python3-apt` (present in the qualification guest). Automatic Ansible module-dependency installation is disabled. Bootstrap uses only the distribution package repository; it does not install Python environments or Galaxy collections. Runtime checks enforce the exact installed Git/Ansible package versions before sync/check. A deliberate OS package update that changes them stops this channel until an explicitly reviewed engine upgrade; freshness and the systemd journal expose the stopped controller. No global APT pin or hold changes the host's OS upgrade policy.

Pinned Debian Ansible was selected because its native package, systemd, and check-mode operations with a small local helper for atomic validated unit publication cover this small payload without extra Python dependency delivery. The controller is still necessary for Git selection, lock/pause, revision reporting, and retry policy. Pyinfra would retain that controller and add a separately pinned Python runtime/dependency installation; no demonstrated benefit justified that extra engine path. This qualification tests Ansible, not a claim of comparative pyinfra performance. A permanent central runner, a privileged container platform, and a general host-management task catalog are unnecessary for this ownership boundary. References: [Ansible apt](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/apt_module.html), [validated templates](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/template_module.html#parameter-validate), and [pyinfra local connector](https://docs.pyinfra.com/en/3.x/connectors/local.html).

## Explicit bootstrap and engine upgrades

Before bootstrap, confirm the intended node identity and exporter ownership, check that port 9100 is free, and repeat the read-only package simulation against current repositories. Review any existing listener before enrolling this channel.

```sh
sudo ss -lntp 'sport = :9100'
sudo apt-get -s --no-install-recommends --no-remove install prometheus-node-exporter=1.9.0-1+b4
```

Review the engine at the chosen repository revision, copy this directory over the approved operator SSH connection, and run `sudo ./bootstrap.sh https://github.com/kelchm/home-lab.git main`. Bootstrap requires a Debian 13 PVE host named `pve-sbx-1`, `pve-sbx-2`, or `pve-sbx-3`; it refuses to adopt an existing unowned exporter. The disposable VM override is narrowly restricted to hostname `monitoring-eval-990`, without `/etc/pve`: `sudo ./bootstrap.sh /tmp/pve-eval/fixture.git main --guest-host-id pve-sbx-1`. Do not use the override for production enrollment.

Bootstrap pauses and stops the controller and settles any recorded transient play before package or engine changes. It marks an existing engine dirty before changing its files, then installs the exact Git/Ansible versions with required dependencies and no recommends or removals, validates engine Python/Ansible syntax and systemd units, explicitly installs the engine, marks reconvergence pending (`dirty=1`), then enables the timer **paused**. Source or package installation failure leaves deployment paused. Re-running bootstrap is the engine-upgrade operation; accepted resume performs one apply/restart even if the Git payload is unchanged, then successful unchanged ticks return to no-op behavior. The applied revision/history is retained. Payload Git changes never upgrade the engine. The installed engine has no dependency on the workstation checkout afterward. Keep Git read access public/read-only; secret distribution and Git authentication setup are outside this slice.

Only `pve-sbx-1` is payload-enabled. Nodes 2/3 are disabled and must not be bootstrapped/enrolled until separately approved. Disabling a node halts future payload applies; it does not uninstall a running exporter. To remove an enrolled payload, keep the node enabled and explicitly set `node_exporter` to `absent`.

Before canary resume, inspect `/etc/pve-hostcfg/config.json`, the reviewed payload, package availability, the systemd units, and the intended node identity. Then run:

```sh
sudo /usr/local/lib/pve-hostcfg/controller.py resume
sudo systemctl start pve-hostcfg.service
sudo journalctl -u pve-hostcfg.service --no-pager -n 100
sudo cat /var/lib/pve-hostcfg/status.json
curl -fsS http://127.0.0.1:9100/metrics
ss -lnt 'sport = :9100'
```

The empty capability set and systemd protections were qualified on Debian 13.7, not on the production PVE 9.2 hosts. Canary acceptance still needs actual PVE host/collector coverage and the subsequent local push path. Promote nodes 2/3 only after that acceptance, with an explicit payload-enrollment change and per-host bootstrap.

## Pause, failures, drift, and reverts

```sh
sudo /usr/local/lib/pve-hostcfg/controller.py pause
sudo /usr/local/lib/pve-hostcfg/controller.py check
sudo /usr/local/lib/pve-hostcfg/controller.py resume
```

Pause is a durable file and persists across reboot. It is written even when the nonblocking apply lock is occupied. It prevents subsequent applies; an already executing Ansible task may finish. For immediate interruption, pause first, then `sudo systemctl stop pve-hostcfg.service`; for a manually launched controller, send SIGTERM to its known PID. The controller stops its exact transient play unit and waits for collection before releasing its lock. An interrupted apply remains dirty and retryable. Resume returns a failure if the lock is busy; retry after the active run ends. Resume permits the next relevant/failed apply; it does not repair ordinary drift at an unchanged successful revision.

While paused, the timer can still fetch desired state and perform a read-only drift check. `check` uses the locally retained **applied** commit without fetching and never repairs or reloads systemd. A successful Ansible exit with `changed > 0` means drift, not a clean host. The applied Git ref keeps that tree reachable even after force pushes/garbage collection. Missing/uncheckable state is unknown (`-1`), never reported clean. A Git outage preserves the service, applied revision, and local check capability. Fetch is bounded to 45 seconds; every controller Ansible syntax/check/apply runs in its own uniquely named transient systemd service with `RuntimeMaxSec` of 30/120/300 seconds, control-group termination, and a five-second stop grace. This contains Ansible workers even when they detach into another process session. Abnormal wrapper timeout also stops that exact unit and waits for it to disappear before drift or lock release. Its name is recorded locally so the next lock holder can settle a play left by a forcibly killed controller. Failure to confirm collection retains the lock. The outer timer service also bounds the full run to ten minutes. Contending sync/check calls exit without touching status.

Before first package installation the default exporter unit is masked, including across an interrupted install. The apt transaction installs no recommended packages and performs no autoremove; it never creates or modifies `/usr/sbin/policy-rc.d`. Read-only package simulations on all three PVE hosts found only the exporter itself would be newly installed, with no dependencies, upgrades, or removals. Recheck that assumption before enrollment if package state changes. Duplicate collectors are rejected in the data schema. A local helper validates the unit and binary flags in a private candidate before a single atomic unit replacement, then the controller starts/restarts the validated loopback unit. Invalid flags preserve the existing mask or valid unit. Read-only checks compare the final unit metadata/bytes without staging a candidate or running its validator. Apply candidates have no systemd unit suffix; ordinary termination removes them, and the next apply removes stale root-owned candidates left by an abrupt kill. Validation errors remain visible in the journal. Every apply reloads systemd; a retry of a previously dirty apply also restarts the exporter even if the already-published template is unchanged. A killed package transaction can require deliberate dpkg recovery; do not interpret the apply marker as transactional rollback.

An apply is not globally atomic: a package or earlier task may have changed before a later failure. Only a fully successful Ansible recap advances `applied` and the success timestamp. Never treat a successful fetch as a successful apply. A revert is a new reviewed desired configuration, not transactional rollback; commit the reverted payload, allow it to converge, and inspect the service/status. To intentionally repair ordinary drift, make a reviewed payload change (or repair the owned artifact manually); unchanged desired commits do not repair it automatically.

## Status contract

`status.json` and `textfile/pve-hostcfg.prom` are each atomically replaced. Revision info metrics label the full desired/applied/attempt SHA separately; applied remains the last fully converged payload revision, even when desired has unrelated commits. Booleans use 0/1 and unavailable observations use `-1`. `dirty=1` means an apply began without a recorded full success, or an explicit engine installation awaits reconvergence after accepted resume. `attempt_success` describes the last apply/validation attempt, not a fetch. `fetch_timestamp_seconds` is the last successful fetch, `attempt_timestamp_seconds` the last attempt observation, `success_timestamp_seconds` the last fully successful apply, `drift_timestamp_seconds` the last check observation, and `status_timestamp_seconds` the last status publication. Alert on stale status/check/fetch timestamps as well as failures; an old success alone proves neither freshness nor current health.

`service_active` comes from actual systemd state; it does not prove every collector is healthy or the port is correctly configured. Inspect `node_scrape_collector_success`, the live listener, the unit, and the journal when accepting or diagnosing a host. Textfile directory contents supplied by other owners are never rewritten or removed. The local status artifact survives explicit exporter removal; external reporting for that state depends on the later push slice or operator inspection.

## Explicit removal

Set the enrolled node's `node_exporter` to `absent` in a reviewed payload commit. The apply stops/disables the exporter first, purges only its exact package (without autoremove), removes only the owned unit override, and reloads systemd. Dependencies, controller state, status, and other textfiles remain. A subsequent explicit `present` payload rebuilds it. Removing a node from the JSON or disabling it is not removal.

To retire the deployment engine after verified payload removal, pause it, stop/disable `pve-hostcfg.timer`, stop `pve-hostcfg.service`, verify no controller owns the lock, and deliberately remove its two units, `/usr/local/lib/pve-hostcfg`, `/etc/pve-hostcfg`, and `/var/lib/pve-hostcfg`. Review any other owners' textfiles before deleting the state directory. Leave Git/Ansible and their dependencies installed unless their ownership is separately reviewed; do not use broad autoremove.

## Qualification

Run `scripts/ci/test-pve-hostcfg.sh` with an actual local `ansible-playbook` on PATH, or set `ANSIBLE_PLAYBOOK` to its absolute path. This uses a real bare Git remote and real Ansible operations against harmless temporary files, without root/system mutation. Its test configuration explicitly selects `unscoped_fixture_executor`; production defaults to root/systemd execution, and the fixture executor is forbidden on PVE. The setting comes only from local engine configuration, never fetched Git data. Installed configuration must be root-owned and not writable by group or others. The fixture covers successful no-op and unrelated commits, immutable committed data, unavailable and hung real Git transports, malformed/duplicate-collector rejection, same-SHA retries, partial failure/recovery, revert after a partial failure, nonblocking locking, pause while locked, restart persistence, applied-tree drift with rc=0/changed=1 without repair, removal, metrics, disabled enrollment, and retained applied history after a force push and Git garbage collection.

`tests/guest.py before-reboot` is destructive and guarded to the pre-created VM 990 hostname, root, and no `/etc/pve`. It requires the fixture bootstrap above and a fresh exporter installation. It uses real Debian packages/systemd and injected validator failure to exercise an interrupted first install, verifies mask-only default-start blocking and byte-identical preservation of an existing policy, watches port 9100 for non-loopback activation, retries the identical revision, checks invalid-flag preservation, Git outage, actual Git reverts, drift without repair, explicit removal/rebuild, and leaves a pending payload paused. Reboot that disposable guest explicitly, then run `tests/guest.py after-reboot` to check the new boot ID, retained exporter, timer enablement, persistent pause, and pending desired/applied separation. Do not run it on a hypervisor. Parent work owns VM cleanup and restoration of its temporary backup exclusion.

`tests/recovery.py` additionally requires that same disposable guest to be paused with an installed loopback exporter. It tests real detached Ansible workers under runtime expiry and abnormal wrapper timeout, waits beyond their original completion deadline to detect escaped writes, exercises manual SIGTERM cleanup and SIGKILL recovery through the next lock holder, interrupts a play between validated unit publication and activation, and verifies same-SHA retry changes the live exporter PID/flags despite unchanged template bytes. It also checks duplicate rejection before publication, clean no-op preservation, mask-only first installation, and policy bytes/mode throughout apply/check/interruption. It restores the original exporter unit and leaves the timer enabled and the controller paused. No reboot is required for these checks.

Qualification results and limitations are recorded in [qualification.md](qualification.md). Repository validation runs the harmless Git/local-Ansible harness with upstream Ansible core 2.19.11 in an isolated environment. Real Debian package and systemd acceptance is the separate disposable-guest qualification; CI never runs the destructive guest test.
