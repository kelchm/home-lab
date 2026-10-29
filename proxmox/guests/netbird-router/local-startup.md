# Node-local NetBird startup snippet

**Prepared, not installed.** [#1096](https://github.com/kelchm/home-lab/issues/1096) removes VM 103's NAS vendor-snippet dependency. Matt approved the design on 2026-10-28; installation and live acceptance still need separate authorization. This procedure does not upgrade NetBird, rename the VM or snippet, restart anything, regenerate the running cloud-init drive, or change NetBird Cloud/UniFi policy. The approved order is this repair, a second routing peer ([#1097](https://github.com/kelchm/home-lab/issues/1097)), then router upgrades.

## Verified starting point

Read-only checks on `pve-sbx-1` at 2026-10-29 02:40–02:51 UTC found VM 103 running (QEMU PID 2364), still named `netbird-pilot-router`, running NetBird 0.79.0 with Management and Signal connected. Its root, EFI and cloud-init disks are all on `local-lvm`; only `cicustom` names NAS storage. Both the current and effective VM configurations agree, with no VM or cloud-init changes pending. `hotplug` is unset, so its effective default is `network,disk,usb`, without `cloudinit`.

| Input | Verified value |
|---|---|
| Current vendor reference | `vendor=library-pve:snippets/netbird-pilot-router.yaml` |
| Intended vendor reference | `vendor=local:snippets/netbird-pilot-router.yaml` |
| Source and intended copy SHA-256 | `1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a` |
| Generated user SHA-256 | `30743c6664239a4ba79b0f514b662c8108a85a1f3b86174a87f1deb326582805` |
| Generated network SHA-256 | `33f78c229874011216fe910d3d6924b554f5b7520990dc47a46fb1a6b7c7d47b` |
| Generated metadata SHA-256 | `fe00410a00e7dcd3b6ad56648c7a679ae2ce4d33f93234700976baa3a0fc59b2` |
| Generated and guest cloud-init instance ID | `64a1a679a081c43f1510b2e2b6cf32a1a6d2abb7` |
| `local` storage | Directory `/var/lib/vz`, content `import,iso,backup,vztmpl`, no `snippets`; `/var/lib/vz/snippets` absent |

The node had about 80 GiB free on `local` and 780 GiB free on `local-lvm`. The snippet is small; this change needs no disk movement. Enabling `snippets` updates **cluster-wide** `/etc/pve/storage.cfg`: every node using storage ID `local` permits that content type. The directories and files remain **node-local**, with no replication and no `shared` flag change. Only node 1 receives this file in this procedure.

The installed `qemu-server` 9.2.11 source establishes the identity behavior: `Cloudinit.pm::nocloud_gen_metadata` hashes user plus network data, while `generate_nocloud` reads vendor data independently. Changing only the vendor path, with identical bytes, leaves the generated instance ID unchanged. `QemuServer.pm::vmconfig_hotplug_pending` commits `cicustom` to the saved config and records the old image value in the cloud-init section. With the current hotplug default it does not regenerate the mounted drive; `pvesh get .../cloudinit` will show the old value and new pending value. The next normal VM start regenerates it. `qm cloudinit update` would regenerate/change the running medium and is deliberately omitted.

`qm cloudinit dump` supports only `user`, `network` and `meta`, and generates those from configuration rather than reading the mounted ISO. It is not a vendor dump, nor proof of the installed medium. These hashes cover this baseline because there are no custom user/network/meta references. If such references appear, stop and review their actual contents and metadata separately before proceeding.

## Installation, after authorization

Use a reachable Main admin session, with local recovery access arranged. Do not run this as an away-only operation. Refresh the readbacks and stop on drift, VM locks, pending changes, a different hotplug setting, or any mismatch with the starting point above. Do not alter the hostname, user, SSH keys, network/MAC, `ciupgrade`, other `cicustom` keys, disk configuration or boot settings to make a check pass. [Cloud-init setting changes](README.md#cloud-init-settings-upgrade-netbird) can trigger package upgrades on a later boot.

On the workstation, check the repository source first:

```sh
shasum -a 256 proxmox/guests/netbird-router/vendor-data.yaml
ssh kelchm@10.32.20.21
sudo -i
bash
```

Run the following in that root Bash session. Evidence contains private VM configuration; retain it privately, and publish only hashes and a concise result. The capture function runs read-only commands and writes evidence locally on the host:

```bash
set -euo pipefail
umask 077
evidence=$(mktemp -d /root/netbird-local-startup.XXXXXX)
api=/nodes/pve-sbx-1/qemu/103
old_vendor=library-pve:snippets/netbird-pilot-router.yaml
new_vendor=local:snippets/netbird-pilot-router.yaml
snippet_sha=1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a
capture() {
    local phase=$1 part
    pvesh get "$api/config" --current 1 --output-format json > "$evidence/$phase.config.json"
    pvesh get "$api/config" --output-format json > "$evidence/$phase.effective.json"
    pvesh get "$api/pending" --output-format json > "$evidence/$phase.pending.json"
    pvesh get "$api/cloudinit" --output-format json > "$evidence/$phase.cloudinit.json"
    pvesh get "$api/status/current" --output-format json > "$evidence/$phase.status.json"
    pvesh get /storage/local --output-format json > "$evidence/$phase.storage.json"
    for part in user network meta; do
        qm cloudinit dump 103 "$part" | sha256sum | cut -d' ' -f1 > "$evidence/$phase.$part.sha256"
    done
    qm guest exec 103 -- sh -c 'set -eu; hostname; cat /var/lib/cloud/data/instance-id; netbird version; sha256sum /var/lib/netbird/default.json' > "$evidence/$phase.identity.json"
    qm guest exec 103 -- netbird status > "$evidence/$phase.netbird.json"
    python3 - "$evidence" "$phase" <<'PY'
import json, pathlib, sys
root, phase = pathlib.Path(sys.argv[1]), sys.argv[2]
for kind in ('identity', 'netbird'):
    result = json.loads((root / f'{phase}.{kind}.json').read_text())
    assert result.get('exited') == 1 and result.get('exitcode') == 0, result
    assert not result.get('out-truncated') and not result.get('err-truncated'), result
PY
}
capture before
sha256sum /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml
```

Inspect the private `before` records against the table: `pending.json` must have no `pending`/delete entries; `cloudinit.json` must have no pending/delete entries. Check the guest's hostname, instance ID, 0.79.0 version and connected status. Retain its profile hash and NetBird overlay address for comparison; never print the profile contents. Record the existing Cloud peer ID from the [read-only export](../../../docs/runbooks/netbird-remote-access.md#read-back-netbird-cloud), so later verification can rule out a replacement enrollment.

Prepare guarded values; this aborts if the reviewed storage shape or vendor-only baseline changed. It replaces only the vendor component. Additional `cicustom` keys cause a stop for review; retain them unchanged if that reviewed procedure is subsequently extended:

```bash
python3 - "$evidence" "$old_vendor" "$new_vendor" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
load = lambda suffix: json.loads((root / f'before.{suffix}.json').read_text())
conf, effective, storage = load('config'), load('effective'), load('storage')
assert {k:v for k,v in conf.items() if k != 'digest'} == {k:v for k,v in effective.items() if k != 'digest'}
assert conf['name'] == 'netbird-pilot-router' and not conf.get('lock')
assert conf.get('hotplug') is None and not conf.get('citype')
assert load('status')['status'] == 'running'
assert not any('pending' in x or x.get('delete') for x in load('pending'))
assert not any('pending' in x or x.get('delete') for x in load('cloudinit'))
expected_hashes = {
    'user':'30743c6664239a4ba79b0f514b662c8108a85a1f3b86174a87f1deb326582805',
    'network':'33f78c229874011216fe910d3d6924b554f5b7520990dc47a46fb1a6b7c7d47b',
    'meta':'fe00410a00e7dcd3b6ad56648c7a679ae2ce4d33f93234700976baa3a0fc59b2',
}
for part, expected in expected_hashes.items():
    assert (root / f'before.{part}.sha256').read_text().strip() == expected, part
assert load('identity')['out-data'].splitlines()[:3] == ['netbird-pilot-router', '64a1a679a081c43f1510b2e2b6cf32a1a6d2abb7', '0.79.0']
assert storage['type'] == 'dir' and storage['path'] == '/var/lib/vz'
assert set(storage['content'].split(',')) == {'import', 'iso', 'backup', 'vztmpl'}
assert not storage.get('content-dirs') and not storage.get('shared')
parts = conf['cicustom'].split(',')
assert parts == ['vendor=' + sys.argv[2]], 'New cicustom keys need review'
updated = ','.join('vendor=' + sys.argv[3] if x.startswith('vendor=') else x for x in parts)
for name, value in {'vm.digest':conf['digest'], 'storage.digest':storage['digest'],
                    'cicustom.before':conf['cicustom'], 'cicustom.after':updated,
                    'content.before':storage['content'], 'content.after':storage['content'] + ',snippets'}.items():
    (root / name).write_text(value)
PY
printf '%s  %s\n' "$snippet_sha" /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml | sha256sum --check --status
test ! -e /var/lib/vz/snippets
pvesm set local --digest "$(cat "$evidence/storage.digest")" --content "$(cat "$evidence/content.after")"
install -d -o root -g root -m 0755 /var/lib/vz/snippets
test ! -e /var/lib/vz/snippets/netbird-pilot-router.yaml
install -o root -g root -m 0644 /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml /var/lib/vz/snippets/netbird-pilot-router.yaml
cmp /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml /var/lib/vz/snippets/netbird-pilot-router.yaml
printf '%s  %s\n' "$snippet_sha" /var/lib/vz/snippets/netbird-pilot-router.yaml | sha256sum --check --status
test "$(pvesm path "$new_vendor")" = /var/lib/vz/snippets/netbird-pilot-router.yaml
test "$(stat -c '%U:%G %a' /var/lib/vz/snippets/netbird-pilot-router.yaml)" = 'root:root 644'
qm set 103 --digest "$(cat "$evidence/vm.digest")" --cicustom "$(cat "$evidence/cicustom.after")"
capture after
```

The path and ownership checks must pass before changing the VM reference. A failed command is a stop, not a reason to continue with later steps. If installation stops after enabling snippets or copying the file, roll back only the steps that completed.

Verify the saved configuration, generated data and guest without restarting anything:

```bash
python3 - "$evidence" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
load = lambda phase, kind: json.loads((root / f'{phase}.{kind}.json').read_text())
for kind, allowed in [('config', {'digest', 'cicustom'}), ('storage', {'digest', 'content'})]:
    before, after = load('before', kind), load('after', kind)
    assert not [k for k in before.keys() | after.keys() if k not in allowed and before.get(k) != after.get(k)]
assert load('after', 'config')['cicustom'] == (root / 'cicustom.after').read_text()
assert load('after', 'effective') == load('after', 'config')
assert set(load('after', 'storage')['content'].split(',')) == set((root / 'content.after').read_text().split(','))
assert not any('pending' in x or x.get('delete') for x in load('after', 'pending'))
changes = [x for x in load('after', 'cloudinit') if 'pending' in x or x.get('delete')]
assert changes == [{'key':'cicustom', 'value':(root / 'cicustom.before').read_text(), 'pending':(root / 'cicustom.after').read_text()}]
for part in ('user', 'network', 'meta'):
    assert (root / f'before.{part}.sha256').read_bytes() == (root / f'after.{part}.sha256').read_bytes(), part
assert load('before', 'identity')['out-data'] == load('after', 'identity')['out-data']
assert load('before', 'status')['pid'] == load('after', 'status')['pid']
assert load('after', 'status')['status'] == 'running'
PY
```

Inspect `after.netbird.json` for Management/Signal connected, the same overlay address and the three resource prefixes; confirm the same enrolled Cloud peer ID. Keep the NAS snippet unchanged for rollback and older restore configurations. Record the installed reference and hashes in #1096 and update the [guest baseline](README.md#vendor-data) and [runbook](../../../docs/runbooks/netbird-remote-access.md#router-startup-dependency) only after these checks pass. Do not claim P6 or autostart acceptance here.

## Acceptance at the next planned host reboot

Wait for the next **separately authorized, already planned** `pve-sbx-1` reboot. Do not reboot solely for this check, stop the router, restart NetBird, unmount NFS or fault the NAS to manufacture acceptance. The repair is intended to eliminate this snippet dependency; it does not establish independence from every NAS service.

After that host boot, retain the host boot time and PVE start-all/VM-start task logs, showing VM 103 started unattended with no manual `qm start` and no snippet-storage failure. Repeat the readbacks above: the saved and image vendor references should now both be local, with no cloud-init change pending; compare generated user/network/meta hashes, guest instance ID, hostname, enrolled peer identity and installed version with the installation evidence. Expect a new QEMU PID after a host reboot. Confirm NetBird reconnects and perform only the client/resource checks authorized for that maintenance. If another intervening change altered the baseline, establish a new reviewed comparison first. Record acceptance in #1096; #625/P6 remains uncredited until that evidence exists.

## Rollback

Rollback also needs authorization. Use the same Main recovery access and evidence directory. Verify the old NAS storage is online and its retained snippet matches the recorded SHA-256 before restoring the reference. If NAS is unavailable, leave the verified local reference in place and diagnose; restoring a missing NAS reference would reintroduce the startup failure.

Before any subsequent start/regeneration, with no intervening configuration changes, restore only `cicustom`. Compare the saved config with the installed evidence before issuing the change; the guard below stops on drift, including later `cicustom` keys. If installation failed before `capture after`, compare the fresh config against `before.config.json` with only the intended vendor substitution; do not use this full-install rollback block without that comparison:

```bash
capture rollback_before
python3 - "$evidence" <<'PYROLLBACK'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
load = lambda phase: json.loads((root / f'{phase}.config.json').read_text())
expected, current = load('after'), load('rollback_before')
assert {k:v for k,v in expected.items() if k != 'digest'} == {k:v for k,v in current.items() if k != 'digest'}
PYROLLBACK
printf '%s  %s\n' "$snippet_sha" /mnt/pve/library-pve/snippets/netbird-pilot-router.yaml | sha256sum --check --status
rollback_digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["digest"])' "$evidence/rollback_before.config.json")
qm set 103 --digest "$rollback_digest" --cicustom "$(cat "$evidence/cicustom.before")"
capture rollback_after
```

After rollback, compare the saved config to `before.config.json` (excluding `digest`), generated hashes and guest identity/version. The cloud-init vendor pending entry should disappear when the saved value again equals the still-mounted NAS image. The running process must remain unchanged; do not run `qm cloudinit update` or restart. If a normal start/regeneration has already happened, reverting the reference can instead leave NAS pending until the next normal start; record that state rather than forcing another restart.

The local file and enabled content type can safely remain for later peers. Remove them only after checking cluster-wide VM/container configs (including snapshots and pending sections), retained archive configurations, every node's local snippets, and any planned second peer: neither this file nor the `snippets` capability may still be needed. The shared storage definition affects every node. Do not clear `snippets` just because VM 103 stopped using it. When those checks establish that this installation is the sole use and no intervening storage change occurred:

```bash
pvesh get /storage/local --output-format json > "$evidence/cleanup.storage.json"
python3 - "$evidence" <<'PYCLEANUP'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
load = lambda phase: json.loads((root / f'{phase}.storage.json').read_text())
before, current = load('before'), load('cleanup')
assert {k:v for k,v in before.items() if k not in {'digest','content'}} == {k:v for k,v in current.items() if k not in {'digest','content'}}
assert set(current['content'].split(',')) == set((root / 'content.after').read_text().split(','))
(root / 'cleanup.digest').write_text(current['digest'])
PYCLEANUP
printf '%s  %s\n' "$snippet_sha" /var/lib/vz/snippets/netbird-pilot-router.yaml | sha256sum --check --status
rm -- /var/lib/vz/snippets/netbird-pilot-router.yaml
rmdir -- /var/lib/vz/snippets
pvesm set local --digest "$(cat "$evidence/cleanup.digest")" --content "$(cat "$evidence/content.before")"
```

Re-read `/storage/local` and confirm only the content flag was restored. Never delete the old NAS snippet while retained archives refer to it. A revert of this documentation PR performs none of this operational rollback.
