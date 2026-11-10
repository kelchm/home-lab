# Second NetBird routing peer

VM 104 is NetBird's second routing peer. It was installed and enrolled on 2026-11-08 under [#1097](https://github.com/kelchm/home-lab/issues/1097) by the sequence below; the [installation evidence](https://github.com/kelchm/home-lab/issues/1097#issuecomment-6729072987) is on that issue. With VM 103 on `pve-sbx-1`, both peers are in the routers group and serve the same three resources. Client failover between them is unmeasured: the independent-path/H1/R1 baseline in [#625](https://github.com/kelchm/home-lab/issues/625) needs its own approved window, followed by rolling upgrades and the stage-2 rename one router at a time. #1096's host-boot acceptance stays open for the next separately authorized planned host boot.

Manage 104 like 103, through the guest agent on its host: `ssh kelchm@pve-sbx-2`, then `sudo qm guest exec 104 -- netbird status`. Main cannot reach the VM's SSH.

## Installed definition

Read back on 2026-11-08:

| Setting | Value |
|---|---|
| VM / name / host | `104` / `netbird-router-2` / `pve-sbx-2` (`10.32.20.22`) |
| Address | `10.32.19.104/24`, gateway/resolver `10.32.19.1`, search `home.kelch.io`, VLAN 19 on `vmbr0` |
| Source / disks | Full clone of Debian 13 template 9000 on node 1, stopped offline migration to node 2; root 8 GiB, EFI and cloud-init on node 2 `local-lvm`, with no `base-9000` dependency |
| Resources / firmware | 1 vCPU, 1024 MiB; inherited `x86-64-v2-AES`, q35/OVMF, existing EFI secure-boot settings and `virtio-scsi-single` |
| Identity | Fresh MAC, SMBIOS UUID, vmgenid, machine ID and NetBird WireGuard key, each distinct from 103 and template 9000; never clone an enrolled router |
| Cloud-init | User `kelchm` with the operator key `SHA256:2GmeRvteq/52xGPMBHWtTC9tJ69pok60DB41bn74M3g` (the same as 103); default `ciupgrade=1`; instance ID `d0de10ad67ebd78d279aea87ac02cd1c1b4602d4` |
| Vendor / helper | [`vendor-data.yaml`](vendor-data.yaml) at `local:snippets/netbird-router-2.yaml`, on `pve-sbx-2` only, root `0644`, SHA-256 `59588885e027ab105251574869960c69998b0253a0ea89bb8aa8a009750b06c5`. The 2026-11-08 installation used an earlier revision that pinned NetBird 0.79.0 (`c9aed81b8f46da6aacb9d5ee12fb43df97043394363cfb5eb09c81876cdd4898`); after the upgrade that day the pin was removed here and the installed copy replaced to match. It embeds the [shared credential-free enrollment helper](../netbird-router/netbird-router-enroll.sh). |
| OS | Debian 13, kernel 6.12.116 after the first-boot upgrade (103 also runs 6.12.116 since 2026-11-08); unattended-upgrades enabled |
| NetBird | `0.82.0` since 2026-11-08, set by [`upgrade-routers.sh`](../netbird-router/upgrade-routers.sh), with no apt preference or hold; peer `netbird-router-2` in `All` and `routers` only, login and inactivity expiration off; client routes and DNS disabled; IPv4 forwarding, kernel WireGuard |
| Routing | Routers group `dasjbl3l0ubs738ubh10` (both peers), Network `dasjblifadhs73btnb40`, router object `dasjboifadhs73btp080`; group metric 9999 and masquerade unchanged |
| UniFi | `10.32.19.104` is in the `NetBird Routers` Network List with `.103`, so both NetBird HTTPS forwards and their generated returns cover it |
| Startup / protection | `onboot=1`, `startup=order=10,up=30`, `protection=1`, tags `netbird;remote-access`; not a PVE HA resource |
| Backup | All-guest `daily-backups`, 05:00 America/New_York. The first post-enrollment archive is due on 2026-11-09 and has not been verified yet. |

The enrolling key was one-off with a one-day expiry and was revoked right after use. From 104, Jellyfin `/health` and Grafana `/api/health` return HTTP 200 with validated TLS, and DNS to `10.32.30.1` answers over UDP and TCP; `10.32.130.1:80`, `10.32.140.1:80`, `10.32.20.21:8006` and `10.32.130.2:53` time out while answering from Main. Its routed-traffic filter matches 103's: clients may reach only TCP 443 on `10.32.140.1` and `10.32.130.1` and port 53 on `10.32.30.1`.

Retired VMIDs and addresses 101/102 stay reserved. Shared gateway, switch/trunks, VLAN, DNS, WAN/relays, Cloud, PVE control plane, software lineage and NAS backups remain common dependencies; physical power-domain independence is unverified.

## First-boot packages and future instances

104 deliberately retains PVE's default Debian first-boot package/security upgrades. Cloud-init performs that package phase before `runcmd`; the NetBird repository is not yet present, so suppressing `ciupgrade` is unnecessary to pin NetBird. The vendor file installs NetBird's current stable package; [`upgrade-routers.sh`](../netbird-router/upgrade-routers.sh) then sets the declared version. The installed revision pinned and held 0.79.0 until the 2026-11-08 upgrade. It starts/enables the guest agent before its own NetBird bootstrap, bounds curl fetches and apt acquisition timeouts, and stops if the repository key check or an apt step fails. The preceding cloud-init package phase still has to complete before those commands run; early guest-agent availability is not guaranteed during that phase.

103 was dist-upgraded on 2026-09-27 and again at its renamed first boot on 2026-11-08. 104's first boot on 2026-11-08 applied 51 Debian upgrades, including kernel 6.12.116, and 104 was rebooted onto that kernel before enrollment. Matching NetBird versions does not mean identical OS packages. The old 103 vendor bytes remain SHA-256 `1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a`; do not rewrite that baseline or its historical snippets.

A future hostname/user/key/network change creates a new cloud-init instance and can upgrade Debian packages/kernel again. Restart immediately after such a change, wait for `cloud-init status --wait` to finish in the guest, then run `upgrade-routers.sh` to return NetBird to the declared version. The 0.79.0 preference and hold were removed from the running VM during the 2026-11-08 upgrade. Apt installation verifies signed repository metadata. Linux 0.79.0's inspected auto-updater is unsupported, but do not infer other clients' update behavior from that.

## Installation sequence

The 2026-11-08 installation followed this sequence. Reuse it, with its own approval, for a rebuild with a new key and peer (see [Withdrawal and identity recovery](#withdrawal-and-identity-recovery)). For a rebuild at `.104`, first remove `.104` from the `NetBird Routers` list, keeping `.103`, so the new guest has no ingress access until the step 5 conflict gate passes; step 6 adds it back. Disruptive H1/R1 tests, hard stops, host reboot/power tests, restores, backup starts, deletion, upgrades and renames are outside it.

### 1. Refresh prerequisites and isolate the clone

Require a working Main-to-PVE administrator session and local recovery, all nodes online/quorate, healthy unchanged 103, fresh capacity/VMID/IP and orphan-disk reads, no concurrent task or maintenance/backup collision, and installed/verified #1096 before activation. Do not stage migration across the 05:00 backup window: an all-guest backup may take a stopped guest's backup lock through a paused prelaunch. Do not add a temporary backup exclusion.

Re-read template 9000 on node 1: require stopped/template status, `onboot=0`, local root/EFI/cloudinit layout and no inherited `cicustom`, hooks, foreign disks, enrollment or secrets. If its definition differs, stop for review; do not edit the template. On node 1, extract only VM 200's `sshkeys` property from its configuration, URL-decode it and create `/run/netbird-router-operator.pub` exclusively with mode `0600`. Validate its fingerprint against the owner-approved operator public key before using it; stop on a missing, different or ambiguous key. Do not print the full VM configuration, copy private keys or `cipassword`, or overwrite an existing unexpected file. Preserve Main's existing management restrictions.

On **node 1**, clone only the clean template; keep the new guest stopped and immediately disable its NIC before migration/staging:

```sh
sudo qm clone 9000 104 --name netbird-router-2 --full 1 --storage local-lvm
sudo qm set 104 \
  --onboot 0 --cores 1 --memory 1024 \
  --net0 virtio,bridge=vmbr0,tag=19,link_down=1 \
  --ipconfig0 ip=10.32.19.104/24,gw=10.32.19.1 \
  --nameserver 10.32.19.1 --searchdomain home.kelch.io \
  --ciuser kelchm --sshkeys /run/netbird-router-operator.pub --ciupgrade 1 \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --startup order=10,up=30 --protection 1 --tags 'netbird;remote-access'
sudo qm disk resize 104 scsi0 8G
sudo qm status 104
```

Require stopped, `onboot=0`, `link_down=1`, no template/backing dependency, all disk owners 104, new MAC/UUID/vmgenid distinct from 103 and template. Never first-boot 104 on node 1. Do not run `qm clone ... --target pve-sbx-2` against this local template and do not clone enrolled 103.

### 2. Migrate only the stopped new guest

On **node 1**:

```sh
sudo qm migrate 104 pve-sbx-2 --online 0 --targetstorage local-lvm
```

Confirm successful task, node-2 config ownership, stopped state and local root/EFI/cloudinit volumes, with no `base-9000` or `vm-103` dependency. Inspect node-1 remnants. No live-migration switch, force/unlock/quorum override or backup is needed. If migration fails or ownership is ambiguous, stop and preserve the protected stopped guest, tasks, config and disks for diagnosis. Never auto-destroy, restart, clone again or hard-stop an ambiguous copy.

### 3. Provision node 2's snippet and verify the definition

[`provision-snippet.sh`](provision-snippet.sh) reuses #1099's guarded destination checks. It preserves cluster-wide `local` content flags, uses the PVE digest for a capability addition if needed, verifies type/path/non-shared layout, refuses symlinks (including dangling ones), rejects nonmatching existing files and checks SHA, owner and mode. New files are staged and published exclusively without overwriting a concurrent destination. It does not change a VM. #1096 should already have enabled the cluster-wide `snippets` capability; that does not copy files to node 2. Keep the original 103 filename/hash and NAS archives intact.

From the reviewed workstation checkout, first check [`SHA256SUMS`](SHA256SUMS). For a future approved transfer:

```bash
(cd proxmox/guests/netbird-router-2 && shasum -a 256 --check SHA256SUMS)
destination=kelchm@10.32.20.22
transfer=$(ssh "$destination" 'umask 077; mktemp -d /tmp/netbird-router-2.XXXXXX')
scp proxmox/guests/netbird-router-2/{vendor-data.yaml,provision-snippet.sh,SHA256SUMS,check-unifi-delta.py} "$destination:$transfer/"
printf 'Reviewed transfer directory: %s\n' "$transfer"
```

In an approved node-2 Bash session, use that exact path, verify all transfer hashes against the reviewed checkout, then invoke the credential-free provisioner:

```bash
transfer='/tmp/netbird-router-2.<this-run>'
(cd "$transfer" && sha256sum --check SHA256SUMS)
sudo bash "$transfer/provision-snippet.sh" "$transfer/vendor-data.yaml" netbird-router-2.yaml \
  59588885e027ab105251574869960c69998b0253a0ea89bb8aa8a009750b06c5
sudo qm set 104 --cicustom vendor=local:snippets/netbird-router-2.yaml --onboot 0
```

Keep harmless partial staging on failure and inspect the printed evidence directory; do not remove the cluster-wide capability or overwrite an unexpected file. Verify the generated 104 definition: only local disks/snippet, no NAS/ISO/hook dependency, intended key fingerprint/network/name, `ciupgrade=1`, `onboot=0`, protection/startup and NIC down. Retain hashes of generated user/network/meta data and instance ID privately, not raw user data or profile contents. The file is node-local and must be explicitly copied/checked on any future destination. Remove only this run's transfer copy after successful verification.

### 4. First boot on node 2 and bootstrap gate

Refresh the IP inventory immediately before this approved network activation. Preserve the generated MAC and **every** `net0` option while changing only `link_down=1` to `link_down=0` using a fresh config and digest. Do not reconstruct `virtio,...` without its MAC. A reviewed command shape, with the exact captured values substituted, is:

```sh
sudo qm set 104 --digest <fresh-config-digest> --net0 '<unchanged-net0-with-only-link_down=0>'
sudo qm start 104
```

Start a 15-minute observation deadline at first boot. On node 2, bound the cloud-init wait in the guest and the agent call, for example `sudo qm guest exec 104 --timeout 850 -- timeout --kill-after=5s 840s cloud-init status --wait`; shorten both timeouts to fit the remaining observation allowance if the guest agent starts late. This stops only the waiting command, not cloud-init. Inspect the nested `exited`, `exitcode` and returned PID explicitly. A PID-only result must be reconciled with `qm guest exec-status 104 <pid>` within that same deadline; do not use enrollment's separate 30-second polling allowance to extend it.

Within the remaining allowance, use bounded agent reads for `systemctl --failed`, active/enabled checks for agent/NetBird, `sysctl net.ipv4.ip_forward`, `netbird version`, `dpkg-query`, `apt-mark showhold`, hostname, address/default route and DNS UDP/TCP 53. A PID-only result or deadline is unresolved, not success. Do not expose full private profiles or apt/auth logs. Require clean cloud-init, intended services, IPv4 forwarding, NetBird installed with no apt hold and the intended host/address. `upgrade-routers.sh` sets the declared NetBird version once 104 is enrolled and serving. Compare machine-ID hashes and NetBird public identity metadata with 103; require a distinct fresh identity, no inherited Cloud enrollment and fresh template-derived IDs. Record kernel/WireGuard/OS skew. At this point HTTPS checks wait for step 6.

On bootstrap failure or an unresolved deadline, preserve the MAC and other NIC options while setting only 104's NIC down and `onboot=0`, then request a graceful shutdown and confirm stopped. If shutdown does not complete, leave it link-down/protected and stop for diagnosis. Do not hard-stop, destroy, enroll or grant firewall access to an unresolved guest.

Only after bootstrap and definition checks pass, set `onboot=1` with a fresh digest, preserving `startup=order=10,up=30` and protection. Startup delay orders guests on one host; it is not a readiness probe, power-on control or host-boot acceptance result.

### 5. Conflict gate before source-IP permissions

Before either UniFi source allowance, freshly read **both** UniFi `stat/sta` and `rest/user` history/fixed-IP tables. Require a current `.104` observation with 104's actual generated MAC and no other MAC, stale conflicting reservation or ambiguous ownership in either table. Missing current observation is unresolved, not a pass. Absence of history alone is insufficient. During the separately approved packet window run inside 104:

```sh
arping -D -c 3 10.32.19.104
```

Require the intended VLAN-19 interface, a functioning L2 path and normal gateway/DNS positive controls; retain command/exit status and response counts. iputils duplicate-detection success means no duplicate replies to those probes, not proof against an offline, sleeping, filtered or hidden static device. Another responder, broken L2/positive controls or ambiguous result fails the gate. On failure, set only 104's NIC down and `onboot=0` and request a graceful shutdown; confirm stopped. If shutdown does not complete, keep it link-down/protected and stop for diagnosis, with no automatic hard stop or destruction. Do not add firewall permissions while this gate is unresolved. No probe was sent during preparation.

### 6. UniFi list edit, then path checks

Since 2026-11-08 both NetBird forwards, `NetBird to Services Ingress` (`6ab93666519d986cdf987240`) and `NetBird to Admin Ingress` (`6ad6c08f519d986cdf2ee284`), match their source through the `NetBird Routers` Network List. With an authorized administrator session, take a complete policy and list snapshot. Just before the edit, re-read the list and both forwards; stop and review if anything differs from the snapshot. Then add `10.32.19.104` to that list in Settings → Networks → Network Lists and update its description, and read the list back. Change nothing else: no policy edit, no subnet, no reuse of retired Tailscale allowances.

UniFi regenerates each forward's generated return from the list's members; this was verified on 2026-11-08 by adding and removing a test member on another list. Afterwards, find each return by `origin_id` and require a matching source VIP/443, destination `10.32.19.103` and `10.32.19.104`, `RESPOND_ONLY` and `RELATED,ESTABLISHED`. Do not write generated returns directly.

The [descriptive delta](unifi-source-delta.json) and the offline checker (`check-unifi-delta.py`) describe the earlier per-rule `source.ips` edit. They predate the list and no longer apply. The checker would reject a list-based change, so do not use it as a gate. Instead, take a second complete snapshot and compare it with the first, ignoring the top-level `hits` and `last_hit` counters, which change with traffic. The only other differences should be the list's members and description, and the two regenerated returns. Concurrent changes need a fresh review, not an overwrite.

**Now**, after approved UniFi edits, require validated TLS Jellyfin `/health` HTTP 200, Grafana `/api/health` HTTP 200 with healthy database and DNS UDP/TCP53 from 104. Retain blocked HTTP80 to both VIPs, PVE `.20.21:8006` and adjacent `.130.2:53` checks with positive endpoint controls and exact scope readback. Record gateway `.30.1:443` behavior from the router; do not make success a gate because the complete zone matrix is unverified. Away-client DNS-resource TCP443 must remain denied by NetBird policy. A timeout to an absent listener alone does not prove a /32 rule.

### 7. Enroll only after both source additions and path gates

Freshly read the stable router group/Network IDs, peer-approval setting, expiration settings, policies referencing `All` or routers and current client auto-update settings. Keep 103 in the group, metric/masquerade/resources/posture unchanged and 104 out of clients. Check the [logging boundary and stdin-only enrollment procedure](../netbird-router/enrollment.md) before creating any short-lived one-use key. Node/guest sudo I/O/audit/debug/auth and Cloud audit behavior are unverified. If stdin capture cannot be excluded, use the owner's assessed private console fallback; do not claim secrecy in uninspected upstream logs.

Use the embedded credential-free helper once, with the private file mechanism and key outside Git/cloud-init. The command for this node/guest is:

```sh
ssh kelchm@10.32.20.22 \
  'sudo -n qm guest exec 104 --pass-stdin 1 --timeout 120 -- /usr/local/sbin/netbird-router-enroll' \
  < "$SETUP_KEY_DELIVERY_FILE"
```

The helper bounds stdin/enrollment, uses a unique `0600` `/run` file and exit/signal cleanup. Poll the exact returned PID/exit code within the shared procedure's limit. A lost session, missing PID or deadline is ambiguous; reconcile Cloud/guest state before any retry. Cleanup cannot survive every SIGKILL/kernel failure. Revoke the one-use key at window end on success, failure or ambiguity, verify revocation from metadata, and verify any enrolled peer remains: key revocation does not delete it. Verify Management/Signal, intended group/peer approval, disabled routes/DNS and the exact three prefixes. No key is created/read by this PR.

### 8. Baseline and backups remain open

[Qualification](qualification.md) defines proposed independent-path/H1/R1 evidence and limits. Do not run it under installation approval alone. The existing `all=1` backup job will include 104 when created, but only a successful **post-enrollment** archive can recover that enrolled identity; verify the next scheduled task and archive. Do not start an immediate backup. #1097 and #625 closed by owner decision without running it; merging documentation completes no live gate.

## Withdrawal and identity recovery

For an approved withdrawal, first prove 103 healthy and serving the away path. Gracefully stop 104 on its actual host, set `onboot=0`/NIC down, withdraw only peer 104 from the routers group (or its peer), revoke any unused key and remove only `.104` from the `NetBird Routers` list; verify the regenerated returns and `.103` service. Preserve friendly renames, all other semantics, the good #1096 repair and old snippets. On an unresponsive/ambiguous guest, keep isolation/protection and diagnose rather than auto-hard-stop/destroy. Leave the protected stopped guest for an explicit disposition.

After failed-node ownership, **rebuild with a new key/peer is the default**. Moving a config owned by a failed host is not routine automatic recovery. Identity-preserving restore is separate and undrilled: fence every old instance, choose an exact successful post-enrollment archive and its archived snippet bytes, establish node/VMID ownership and local storage capability, restore without `--unique` or incidental name/network/cloud-init edits, re-protect and verify before start. Never restore 103's identity into 104 or run two copies of one identity. `vzdump` does not independently archive the external host snippet; retain every filename/hash referenced by old archives, including 103's historical NAS file. Backups share NAS; neither host/power/backend failure nor restore acceptance is established here.
