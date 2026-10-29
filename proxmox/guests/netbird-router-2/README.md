# Second NetBird routing peer

**Planned, not allocated or installed.** This definition prepares [#1097](https://github.com/kelchm/home-lab/issues/1097). VM 103 remains the only routing peer and still uses its NAS vendor reference. [#1099](https://github.com/kelchm/home-lab/pull/1099) prepares the local-startup repair; it has not been installed. Merging these files deploys nothing: Flux reconciles `kubernetes/apps`, and PVE, NetBird Cloud and UniFi changes require an administrator's separate approved window. No key, guest, firewall edit or failover test was created by preparation.

The approved design order is **103 local-startup repair (#1096) → second peer (#1097) → independent-path/H1/R1 baseline → rolling upgrades and stage-2 rename**. Install #1096 and verify 103's unchanged identity, version and generated cloud-init data before activating 104. Its actual host-boot acceptance remains open for the next separately authorized planned host boot; do not delay redundancy solely to manufacture a power cycle, credit #625/P6 early or fault shared NAS storage.

## Proposed definition and evidence

| Setting | Proposed value |
|---|---|
| VM / name / host | `104` / `netbird-router-2` / `pve-sbx-2` (`10.32.20.22`) |
| Address | `10.32.19.104/24`, gateway/resolver `10.32.19.1`, search `home.kelch.io`, VLAN 19 on `vmbr0` |
| Source / disks | Full clone of Debian 13 template 9000 on node 1, stopped offline migration to node 2; root 8 GiB, EFI and cloud-init on node 2 `local-lvm` |
| Resources / firmware | 1 vCPU, 1024 MiB; inherited `x86-64-v2-AES`, q35/OVMF, existing EFI secure-boot settings and `virtio-scsi-single` |
| Identity | New generated MAC, SMBIOS UUID, vmgenid, machine ID and NetBird identity; never clone an enrolled router |
| Cloud-init | Existing approved operator public SSH key and user `kelchm`; retain default `ciupgrade=1` |
| Vendor / helper | [`vendor-data.yaml`](vendor-data.yaml) at `local:snippets/netbird-router-2.yaml`; embeds the [shared credential-free enrollment helper](../netbird-router/netbird-router-enroll.sh) |
| NetBird | Exact `0.79.0`, apt preference plus hold; client routes/DNS disabled at enrollment; IPv4 forwarding, kernel WireGuard |
| Routing | Existing routers group `dasjbl3l0ubs738ubh10`, Network `dasjblifadhs73btnb40`, router object `dasjboifadhs73btp080`; group metric 9999 and masquerade preserved |
| Startup / protection | Staging `onboot=0`, NIC down, stopped; after bootstrap/definition validation `onboot=1`, `startup=order=10,up=30`, `protection=1`; no PVE HA relocation |
| Backup | Existing all-guest `daily-backups`, 05:00 America/New_York, existing retention and destination; verify the first successful post-enrollment archive later |

Saved read-only inventory from 2026-10-29 supports this placement: VMID 104 and local-lvm volumes were absent across three nodes; node 2 was online/quorate with about 28.75 GiB available RAM and 793.80 GiB local-lvm capacity. VLAN 19 was allowed on its bridge. UniFi active/history tables showed no `.104` assignment; DHCP is disabled on VLAN 19. These are dated observations, **not a reservation**, a conflict proof or a hardware/power-reliability result. Preserve retired 101/102 and their addresses/archives. Fresh allocation/capacity/task/ownership checks are required in the approved window.

PVE's installed clone/migration source supports full clone locally on node 1 followed by stopped migration of 104 to node 2. Direct cross-host clone of local-only template/storage is rejected. Template, 103 and its availability stay untouched. Clone/migration/bootstrap and the new guest's node-2 datapath are unexecuted. Shared gateway, switch/trunks, VLAN, DNS, WAN/relays, Cloud, PVE control plane, software lineage and NAS backups remain dependencies; physical power-domain independence is unverified.

## First-boot packages and future instances

104 deliberately retains PVE's default Debian first-boot package/security upgrades. Cloud-init performs that package phase before `runcmd`; the NetBird repository is not yet present, so suppressing `ciupgrade` is unnecessary to pin NetBird. The new vendor file explicitly installs `netbird=0.79.0`, writes an apt preference and holds that package. It starts/enables the guest agent before its own NetBird bootstrap, bounds curl fetches and apt acquisition timeouts, and stops on key/version failure. The preceding cloud-init package phase still has to complete before those commands run; early guest-agent availability is not guaranteed during that phase.

103 was dist-upgraded on 2026-09-27; 104 uses the template and Debian packages available at its later first boot. Record both kernels, package skew, WireGuard module and unattended-upgrades configuration in the baseline. Matching NetBird versions does not mean identical OS packages. The old 103 vendor bytes remain SHA-256 `1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a`; do not rewrite that baseline or its historical snippets.

A future hostname/user/key/network change creates a new cloud-init instance and can upgrade Debian packages/kernel again. Handle that and a stage-2 rename in an explicit future window after the baseline. 104's NetBird hold/preference must be deliberately replaced with the reviewed next version during its separately approved rolling upgrade. Never silently fall back to latest if 0.79.0 is unavailable. The preparation verified the public 0.79.0 package hash (`1fd041f1452fe4e0c9dfff3a51ba11087e465c7d11004d6852bea4887f02566f`); actual apt installation must freshly verify signed repository metadata. Linux 0.79.0's inspected auto-updater is unsupported, but do not infer other clients' update behavior from that.

## Approved installation sequence

This is a staged procedure, not an execution record or a request to run it now. After review, root's concrete activation request covers allocation of 104/`.104` on node 2; installing/verifying #1096 on 103; the reviewed stopped clone/migration/snippet/bootstrap, conflict probes and network checks; and administrator UniFi/Cloud enrollment changes. It explicitly names the remaining #1096 host-boot/P6 acceptance gate. Disruptive H1/R1 tests require a later approval. Hard stops, host reboot/power tests, restores, backup starts, deletion, upgrades and renames are outside that installation request.

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
  c9aed81b8f46da6aacb9d5ee12fb43df97043394363cfb5eb09c81876cdd4898
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

Within the remaining allowance, use bounded agent reads for `systemctl --failed`, active/enabled checks for agent/NetBird, `sysctl net.ipv4.ip_forward`, `netbird version`, `dpkg-query`, `apt-mark showhold`, hostname, address/default route and DNS UDP/TCP 53. A PID-only result or deadline is unresolved, not success. Do not expose full private profiles or apt/auth logs. Require clean cloud-init, intended services, IPv4 forwarding, exactly 0.79.0 held and the intended host/address. Compare machine-ID hashes and NetBird public identity metadata with 103; require a distinct fresh identity, no inherited Cloud enrollment and fresh template-derived IDs. Record kernel/WireGuard/OS skew. At this point HTTPS checks wait for step 6.

On bootstrap failure or an unresolved deadline, preserve the MAC and other NIC options while setting only 104's NIC down and `onboot=0`, then request a graceful shutdown and confirm stopped. If shutdown does not complete, leave it link-down/protected and stop for diagnosis. Do not hard-stop, destroy, enroll or grant firewall access to an unresolved guest.

Only after bootstrap and definition checks pass, set `onboot=1` with a fresh digest, preserving `startup=order=10,up=30` and protection. Startup delay orders guests on one host; it is not a readiness probe, power-on control or host-boot acceptance result.

### 5. Conflict gate before source-IP permissions

Before either UniFi source allowance, freshly read **both** UniFi `stat/sta` and `rest/user` history/fixed-IP tables. Require a current `.104` observation with 104's actual generated MAC and no other MAC, stale conflicting reservation or ambiguous ownership in either table. Missing current observation is unresolved, not a pass. Absence of history alone is insufficient. During the separately approved packet window run inside 104:

```sh
arping -D -c 3 10.32.19.104
```

Require the intended VLAN-19 interface, a functioning L2 path and normal gateway/DNS positive controls; retain command/exit status and response counts. iputils duplicate-detection success means no duplicate replies to those probes, not proof against an offline, sleeping, filtered or hidden static device. Another responder, broken L2/positive controls or ambiguous result fails the gate. On failure, set only 104's NIC down and `onboot=0` and request a graceful shutdown; confirm stopped. If shutdown does not complete, keep it link-down/protected and stop for diagnosis, with no automatic hard stop or destruction. Do not add firewall permissions while this gate is unresolved. No probe was sent during preparation.

### 6. UniFi administrator source-only edit, then path checks

The [descriptive delta](unifi-source-delta.json) is not a request payload. With an authorized administrator session, freshly read both forwards and their generated returns. Use the supported administrator UI; an API alternative requires verifying the installed endpoint's full-object PUT semantics and a complete fresh semantic diff first. Saved read-only authentication is not write authority and must not be escalated or probed for writes now.

Change only `source.ips` from `["10.32.19.103"]` to `["10.32.19.103","10.32.19.104"]` for forward IDs `6ab93666519d986cdf987240` (services, destination `10.32.140.1`) and `6ad6c08f519d986cdf2ee284` (admin, destination `10.32.130.1`). Preserve IPv4/TCP443, zones, schedule, enablement, order, `.103`, current friendly names/descriptions and all other semantics. Never add a subnet or reuse retired Tailscale allowances. Find each generated response by `origin_id`; require matching source VIP/443, destination `.103/.104`, `RESPOND_ONLY` and `RELATED,ESTABLISHED`. Do not write generated returns independently.

The offline checker requires complete firewall-policy inventories before and after the edit (a list or a `netbird_policies` object containing every policy, not just the four NetBird objects):

```sh
python3 proxmox/guests/netbird-router-2/check-unifi-delta.py prepare before.json > forward-candidates.json
python3 proxmox/guests/netbird-router-2/check-unifi-delta.py verify before.json after.json
```

It makes no requests. `prepare` preserves fresh friendly renames and emits forward objects only for review, not transmission. `verify` requires the same policy IDs, the exact two forward/generated-return changes and unchanged semantics for every other policy, including unknown fields; only top-level `hits` and `last_hit` counters may change. A filtered snapshot cannot establish that no other policy changed. Concurrent changes require a refreshed review rather than overwriting them. Re-read just before any actual write and read back afterward. A generated-return ID change needs fresh inspection, not an independent write.

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

[Qualification](qualification.md) defines proposed independent-path/H1/R1 evidence and limits. Do not run it under installation approval alone. The existing `all=1` backup job will include 104 when created, but only a successful **post-enrollment** archive can recover that enrolled identity; verify the next scheduled task and archive. Do not start an immediate backup. Keep #1097/#625 open until their actual acceptance evidence exists; merging documentation completes no live gate.

## Withdrawal and identity recovery

For an approved withdrawal, first prove 103 healthy and serving the away path. Gracefully stop 104 on its actual host, set `onboot=0`/NIC down, withdraw only peer 104 from the routers group (or its peer), revoke the key and remove only `.104` from both forward source arrays; verify generated returns and `.103` service. Preserve friendly renames, all other semantics, the good #1096 repair and old snippets. On an unresponsive/ambiguous guest, keep isolation/protection and diagnose rather than auto-hard-stop/destroy. Leave the protected stopped guest for an explicit disposition.

After failed-node ownership, **rebuild with a new key/peer is the default**. Moving a config owned by a failed host is not routine automatic recovery. Identity-preserving restore is separate and undrilled: fence every old instance, choose an exact successful post-enrollment archive and its archived snippet bytes, establish node/VMID ownership and local storage capability, restore without `--unique` or incidental name/network/cloud-init edits, re-protect and verify before start. Never restore 103's identity into 104 or run two copies of one identity. `vzdump` does not independently archive the external host snippet; retain every filename/hash referenced by old archives, including 103's historical NAS file. Backups share NAS; neither host/power/backend failure nor restore acceptance is established here.
