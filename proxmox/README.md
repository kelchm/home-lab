# Proxmox VE

This directory holds the Git-managed baseline and operator documentation for the `pve-sbx` cluster. PVE is intentionally independent of Flux and Kubernetes: changes are reviewed here, then applied deliberately from an admin workstation or directly on a node.

## Live cluster

| Node | Management | Storage and migration |
|---|---|---|
| `pve-sbx-1` | `10.32.20.21` | `10.32.25.21` |
| `pve-sbx-2` | `10.32.20.22` | `10.32.25.22` |
| `pve-sbx-3` | `10.32.20.23` | `10.32.25.23` |

The cluster name is `pve-sbx`. Corosync link 0 uses the management addresses with priority 20; link 1 uses the physically separate 2.5 GbE storage addresses with priority 10. Secure migration is pinned to `10.32.25.0/24`.

Use an individual node URL such as `https://pve-sbx-1.home.kelch.io:8006/`; there is no cluster VIP. The local `kelchm@pve` administrator exists, but TOTP enrollment is still pending ([#1509](https://github.com/kelchm/home-lab/issues/1509)). Root and bootstrap credentials stay outside Git.

## Operator access

The PVE realm account `kelchm@pve` controls UI and API authorization; it is separate from the Linux account on each host. All three nodes also have a consistent `kelchm` Unix account at UID/GID 1000, with a locked password, the `personal:home-lab` ED25519 public key, membership in `sudo`, and an exact `/etc/sudoers.d/90-kelchm` rule granting noninteractive administrative access. Use it for normal host sessions:

```sh
ssh kelchm@NODE
sudo -i
```

PVE retains its cluster-managed `root@pve-sbx-*` SSH keys for node-to-node operations. The human `personal:home-lab` key is not authorized directly for root; ordinary administration and the encrypted configuration-capture workflow connect as `kelchm` and elevate with sudo.

### Service accounts

`homepage@pve` is a read-only account for the Homepage Proxmox widget. Its privilege-separated API token `homepage@pve!homepage` holds `PVEAuditor` on `/`, as does the user; the token secret lives only in `kubernetes/apps/homepage/homepage/app/secret.sops.yaml`. Created 2026-09-26. To rotate it, remove and re-add the token with `pveum user token`, re-grant the ACL, and update the Secret.

## Certificates

Each node serves an independently issued Let's Encrypt certificate for its exact `pve-sbx-N.home.kelch.io` FQDN. The cluster ACME account is `letsencrypt-production`, and the DNS-01 plugin is `pve-sbx-acme-dns01`, limited to the three PVE nodes and backed by a dedicated Cloudflare token with DNS Edit and Zone Read only for `kelch.io`. The authoritative token remains in 1Password; PVE's encrypted recovery capture contains the cluster runtime copy. Do not reuse the Kubernetes cert-manager credential.

PVE's active `pve-daily-update.timer` handles renewal. Check node configuration, timer state, and the certificate actually served on port 8006:

```sh
ssh kelchm@NODE "sudo pvenode config get | grep -E '^acme|^acmedomain'; systemctl is-active pve-daily-update.timer"
openssl s_client -connect NODE_IP:8006 -servername NODE_FQDN -verify_hostname NODE_FQDN </dev/null
```

The initial certificates were issued and hostname-validated on 2026-08-27. A manual `pvenode acme cert renew` correctly refuses while a certificate is outside PVE's 30-day renewal window; do not use `--force` merely to test routine renewal.

## Shared storage

| PVE storage ID | Current NFS export | Content |
|---|---|---|
| `library-pve` | `10.32.25.5:/volume1/library-pve` | ISO images, CT templates, snippets, and imports |
| `backups-pve-sbx` | `10.32.25.5:/volume1/backups-pve-sbx` | VZDump backups |

Both stores use hard NFSv4.1 mounts and are allowed only from the three PVE storage addresses. `library-pve` is platform-specific but reusable across PVE clusters; `backups-pve-sbx` is intentionally cluster-specific. Shared external storage uses the DSM shared-folder leaf as its PVE storage ID so the same resource has one canonical name across both systems.

The cluster job `daily-backups` backs up all non-disposable guests to `backups-pve-sbx` at 05:00 America/New_York in snapshot mode with Zstandard compression. The live job uses `all=1`; add every disposable VMID to its explicit `exclude` field. Since 2026-10-28 the `exclude` field is unset, so the job covers every guest, including `103` (`netbird-router-1`) and template `9000`. Its retention policy is last 3, daily 7, weekly 4, and monthly 6. The built-in matcher currently targets `mail-to-root`, but direct delivery to Gmail failed with `550 5.7.1`; do not depend on email alerts until an authenticated SMTP relay is configured and tested ([#1510](https://github.com/kelchm/home-lab/issues/1510)).

### Backup alerting

Installed on 2026-10-04: the cluster-wide `pve-backup-metrics` webhook and `pve-backup-results` matcher report completed `vzdump` runs to each node's local vmagent. `PVEBackupFailed` and `PVEBackupStale` route through the existing vmalert → VMAlertmanager → Pushover path, warning on failed runs and when an expected node has no clean `daily-backups` completion within 30 hours. A deliberately failed scheduled backup of an isolated guest produced the central metric, an unsilenced warning alert and one Pushover delivery attempt with zero failures; the operator confirmed receiving `backup-alert-test` in Pushover, recorded in #720. Its scheduled retry succeeded, and all test artifacts were removed. On 2026-10-05, the 05:00 production backups finished successfully on all three nodes and their scheduled completion samples reached VictoriaMetrics. Both backup alerts cleared; the startup silence was removed. This reports per-node job completion, not per-guest archive age or restorability. Design, limits, manual installation and removal, and acceptance are in [backup completion metrics](monitoring/backup-metrics/README.md). Git does not manage or revert notification configuration under `/etc/pve`.

## Guest trunk

Every node's RTL8125-backed `vmbr0` is VLAN-aware with an explicit `bridge-vids 10 19 21 25 90` allowlist. The corresponding UniFi `pve-guest-trunk` profile has no native network and carries only those five tagged VLANs on Lab Switch ports 13–15. An untagged guest therefore fails closed, and adding a new guest VLAN requires an intentional change on both PVE and UniFi.

## Persistent guests

| VMID | Guest | Address | Purpose | Source |
|---|---|---|---|---|
| `103` | `netbird-router-1` | `10.32.19.103` | NetBird routing peer for remote access | [`guests/netbird-router`](guests/netbird-router/) |
| `104` | `netbird-router-2` | `10.32.19.104` | Second NetBird routing peer, on `pve-sbx-2` | [`guests/netbird-router-2`](guests/netbird-router-2/) |
| `200` | `hermes-1` | `10.32.21.100` | Hermes Agent, dashboard, and client API | [`guests/hermes-1`](guests/hermes-1/) |

Each guest was provisioned by hand, as its source directory records; whether guests should instead be declared in IaC, and where its state would live, is undecided ([#1511](https://github.com/kelchm/home-lab/issues/1511)). All three guests are enabled at host boot and intentionally not PVE HA resources. `hermes-1` is covered by `daily-backups` and keeps its application state on its local VM disk under `/srv/hermes`. `netbird-router-1` holds no application state beyond its NetBird peer identity. It is covered by `daily-backups` and has `protection: 1`, as does VM 104. The first scheduled post-enrollment backup was verified on 2026-10-29: VM 103 completed at 09:00:17 UTC with a 623,690,275-byte archive and enclosing task `OK` at 09:01:44 UTC (#625). It predates the node-local snippet installation; no archive was unpacked, restored or booted, so identity recovery remains undrilled. Its guest definition covers restore, rebuild and re-enrollment, including snippet provisioning and the limits of the old pre-enrollment archive.

VM 103's boot disk, EFI disk and cloud-init drive are local. The [node-local startup repair](guests/netbird-router/local-startup.md#accepted-installation-2026-10-29) was installed and verified at 11:56 UTC on 2026-10-29: the saved vendor reference became `local:snippets/netbird-pilot-router.yaml`, with unchanged vendor bytes, identity and running process. On 2026-11-08 VM 103 was renamed `netbird-router-1` and pointed at the byte-identical `local:snippets/netbird-router-1.yaml`; the old-named local file stays for archives from 2026-10-30 to 2026-11-08. Only `snippets` was added to the cluster-wide `local` content flags; files remain node-local and must be provisioned on each chosen restore/migration node. Captures by the current [configuration recovery script](#configuration-recovery-archive) include each node's snippet files; the 2026-08-27 capture predates them. Retain the NAS copy for older archives. Host-boot recovery on 2026-10-25 required a manual start. The separately authorized 2026-11-11 host reboot verified unattended startup: VM 103 started about 23 seconds after the 01:58:07 UTC boot, retained its identity/version and reconnected to Management and Signal; VM 200 also returned automatically ([acceptance](guests/netbird-router/local-startup.md#accepted-host-boot-2026-11-11), #1096).

## Second routing peer

[VM 104 `netbird-router-2`](guests/netbird-router-2/README.md) at `10.32.19.104` on `pve-sbx-2` was installed and enrolled on 2026-11-08 (#1097). It has local root/EFI/cloud-init disks and a node-local snippet on `pve-sbx-2`, `onboot=1` and `protection=1`. It is covered by `daily-backups`; its first post-enrollment archive is due on 2026-11-09 and has not been verified. Both routers run NetBird 0.82.1 since 2026-11-11, set by [`upgrade-routers.sh`](guests/netbird-router/upgrade-routers.sh), which Renovate keeps current. Away clients moved between the routers without help during the 2026-11-08 upgrades; failover timing was not measured. VMIDs and addresses 101/102 remain reserved.

## Retired guests

VMs 101 `tailscale-router-1` (`pve-sbx-2`) and 102 `tailscale-router-2` (`pve-sbx-3`) were the Tailscale subnet routers on VLAN 19. Tailscale was retired on 2026-10-25; both VMs and their local disks were destroyed on 2026-10-28 under [#1069](https://github.com/kelchm/home-lab/issues/1069). Their 28 vzdump archives, dated 2026-09-20 through 2026-10-27, remain on `backups-pve-sbx`. Nothing prunes them automatically, they have no offsite copy, and no restore has been tested. Keep VMIDs 101 and 102 reserved while the archives are kept: backups of a new guest with either ID would apply retention to those archives. Their design and acceptance evidence remain in the [remote-admin plan](../docs/plans/20260902-tailscale-remote-admin.md).

## Applied guest network policy

Three UniFi policies contain the general-purpose Workloads VLAN without changing Main administration, `services-prod`, or Internet access:

| Policy | Source | Destination | Action |
|---|---|---|---|
| `Workloads to Protected Networks` | `Workloads` | `Infra Mgmt`, `Storage`, `K8s Prod` | Block all |
| `Workloads to Admin Prod` | `Workloads` | `10.32.130.0/24` in UniFi's `External` destination zone | Block all |
| `K8s Prod to Workloads` | `K8s Prod` | `Workloads` | Block all |

The `admin-prod` pool is a BGP-routed prefix rather than a UniFi network. Network 10.5.67 classified that path in the `External` destination zone during live testing; an otherwise identical `Internal`-zone rule did not match `10.32.130.1`. Keep the explicit routed rule above the default allow rules and retest after UniFi upgrades.

Validated from a disposable VLAN 21 guest on 2026-08-27: PVE management and storage addresses, NAS NFS, the Talos API, and `admin-prod` were blocked; `services-prod`, local DNS, and Internet HTTP remained reachable. A pod on `k8s-prod` could not initiate TCP to the guest, while the Main admin workstation retained SSH access. The complete applied record lives in [`network/unifi/README.md`](../network/unifi/README.md).

## Routine checks

Run cluster-wide checks from any node:

```sh
pvecm status
pvecm nodes
pvesm status
pvesh get /cluster/backup
pvesh get /cluster/resources --type vm
```

Confirm both Corosync links from every node before maintenance:

```sh
pvecm status
corosync-cfgtool -s
corosync-cfgtool -n
journalctl -u corosync --since today --no-pager
```

Inspect the output on each node and confirm that Link 0 and Link 1 report every peer as connected. `pvecm status` proves membership and quorum, and the journal provides history, but neither substitutes for the live per-link output. Do not use the `corosync-cfgtool` exit status alone as proof of link health.

Check the local host before and after storage-heavy work:

```sh
systemctl --failed
journalctl -k -b --no-pager | grep -Ei 'nvme|PCIe|AER|I/O error|reset|timeout'
nvme smart-log /dev/nvme0
nvme error-log /dev/nvme0
pvesm status
lvs -a -o lv_name,lv_size,data_percent,metadata_percent
```

Any NVMe reset, timeout, namespace loss, media error, or PCIe/AER event fails the storage acceptance gate and must be investigated rather than cleared from the log.

As of 2026-08-27 14:48 EDT, commissioning had observed three correctable AER events on node 1, two on node 2, and two on node 3 at the SN770 endpoint. The first two node-1 events and both node-2 events paired a root-port Data Link Layer timeout with an endpoint Physical Layer `RxErr`; the later node-1 event and both node-3 events contained the endpoint `RxErr` without a root-port timeout. Node 3's two events appeared after it had completed a bounded 100-cycle idle-to-read screen without reproducing one, so that screen is not sufficient evidence of stability. All three drives still reported zero SMART critical warnings, media errors, and NVMe error-log entries, with no reset, controller loss, namespace loss, or guest I/O error.

At 17:26 EDT, firmware-level **PCI Express Power Management** was disabled on all three nodes, one node and reboot at a time. Post-boot `lspci` showed the `00:1d.0` root port as `ASPM not supported` and `ASPM Disabled`, and the `02:00.0` SN770 endpoint as `ASPM Disabled`; Linux NVMe APST remained at its default `100000` µs latency threshold. Every node rejoined both cluster quorum and active NFS storage with zero failed units, zero SMART critical warnings, zero media or NVMe error-log entries, and no PCIe, NVMe, reset, timeout, or I/O error in the new boot. This is the applied mitigation baseline, not clearance of the zero-AER gate; the three-node idle and I/O acceptance window must still be repeated ([#1507](https://github.com/kelchm/home-lab/issues/1507)).

## Monitoring

Host metrics come from the [monitoring project](monitoring/README.md), which doco-cd deploys from `main` once a node is enrolled; cluster, guest, storage and backup-coverage state comes from the Proxmox API. Both are described in the [host monitoring runbook](../docs/runbooks/host-monitoring.md).

## Backup and isolated restore drill

Create an on-demand snapshot backup:

```sh
vzdump VMID --storage backups-pve-sbx --mode snapshot --compress zstd
pvesm list backups-pve-sbx --vmid VMID
```

Restore to an unused VMID on the intended node, then remove or disconnect every NIC before first boot. Never boot a restored copy beside its source with the same network identity.

```sh
qmrestore /mnt/pve/backups-pve-sbx/dump/ARCHIVE.vma.zst RESTORE_VMID --storage local-lvm --unique 1
for nic in $(qm config RESTORE_VMID | sed -n 's/^\(net[0-9]\+\):.*/\1/p'); do
    qm set RESTORE_VMID --delete "$nic"
done
restored_config=$(qm config RESTORE_VMID) || { echo "Unable to read restored VM configuration" >&2; exit 1; }
remaining_nics=$(printf '%s\n' "$restored_config" | sed -n 's/^\(net[0-9]\+\):.*/\1/p')
if [ -n "$remaining_nics" ]; then
    echo "Refusing to start RESTORE_VMID: network interfaces remain: $remaining_nics" >&2
    exit 1
fi
printf '%s\n' "$restored_config"
qm start RESTORE_VMID
qm guest cmd RESTORE_VMID ping
```

After validating the recovered filesystem and application state, shut down the isolated copy. Destroy it only when the VMID, node, and disposable purpose have been rechecked.

The initial acceptance drill used disposable VM 300. A 60-second 70/30 random read/write `fio` run on node 1 completed without error, an online local-disk migration to node 2 used VLAN 25 with 252 ms guest downtime, and a guest-agent-consistent 334 MB VZDump archive completed in 14 seconds. The archive restored on node 3 as VM 301, booted with its NIC removed, and passed filesystem and QEMU guest-agent checks. Both disposable VMs were then destroyed; `backups-pve-sbx:backup/vzdump-qemu-300-2026_08_27-11_35_57.vma.zst` remains as the recovery artifact. Restore elapsed time was not captured, so the RTO/restore-throughput acceptance measurement remains open ([#1513](https://github.com/kelchm/home-lab/issues/1513)).

## Host baseline artifacts

[`host/pve-remove-nag.sh`](host/pve-remove-nag.sh) and [`host/no-nag-script`](host/no-nag-script) are copied verbatim from the Proxmox VE Helper-Scripts post-install implementation at commit [`519f5630cecdcf030a22934cba815c6c1dde2b6d`](https://github.com/community-scripts/ProxmoxVE/blob/519f5630cecdcf030a22934cba815c6c1dde2b6d/tools/pve/post-pve-install.sh#L559-L610). They suppress the desktop and mobile subscription reminders and reapply the maintained patch after package transactions. They do not enable enterprise repositories. The upstream MIT license is retained in [`host/LICENSE.community-scripts`](host/LICENSE.community-scripts).

Restore the packaged widget toolkit before applying the artifacts so a previous or changed patch is not layered underneath. Apply and verify one node at a time:

```sh
scp proxmox/host/pve-remove-nag.sh proxmox/host/no-nag-script kelchm@NODE:/tmp/
ssh kelchm@NODE 'sudo rm -f /etc/apt/apt.conf.d/99-pve-no-subscription-popup /usr/local/sbin/pve-no-subscription-popup && sudo apt-get install --reinstall -y proxmox-widget-toolkit && sudo install -o root -g root -m 0755 /tmp/pve-remove-nag.sh /usr/local/bin/pve-remove-nag.sh && sudo install -o root -g root -m 0644 /tmp/no-nag-script /etc/apt/apt.conf.d/no-nag-script && sudo /usr/local/bin/pve-remove-nag.sh'
ssh kelchm@NODE "sudo grep -F NoMoreNagging /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js && sudo grep -F 'MANAGED BLOCK FOR MOBILE NAG' /usr/share/pve-yew-mobile-gui/index.html.tpl"
```

After installation, clear the browser cache or perform an empty-cache hard reload before testing a new login. If a future PVE update defeats the suppression, check the maintained upstream implementation and review its current diff before refreshing these pinned artifacts.

## Configuration recovery archive

[`capture-host-config.sh`](capture-host-config.sh) streams cluster and node configuration directly over SSH into age-encrypted archives under the ignored `.private/pve-sbx/recovery/` directory. The plaintext tar stream is never written to the workstation. The capture includes `/etc/pve`, a SQLite-consistent snapshot of `/var/lib/pve-cluster/config.db`, the ACME account, DNS plugin credential and proxy certificate keys, Corosync authentication, system account and sudo state, root and operator SSH authorization, network and repository state, SSH host identity, LVM metadata, the node-local cloud-init snippets in `/var/lib/vz/snippets` when that directory exists, installed package versions, the kernel boot log, and NVMe identity and health data, so treat the resulting archive as a secret even though it is encrypted. The rest of `/var/lib/vz` (guest disks, ISO images and backups) and the generated pmxcfs status files such as `/etc/pve/.clusterlog` are deliberately excluded; this is a configuration capture, not a host image.

```sh
PVE_AGE_IDENTITY=age.key ./proxmox/capture-host-config.sh
```

The script connects as `kelchm` by default and requires the verified noninteractive sudo policy. Set `PVE_SSH_USER` only for an explicitly tested recovery identity; direct human root SSH is not the normal path. `PVE_AGE_IDENTITY` defaults to `age.key` and `PVE_RECOVERY_DIR` to `.private/pve-sbx/recovery`. The script needs Bash (macOS's 3.2 is enough), `age`, `age-keygen`, `ssh`, `tar` and `sha256sum` locally, and `timeout` from coreutils on the node. SSH runs with `BatchMode=yes`, so a missing key, locked agent or unknown host key fails at once instead of prompting; connections time out after 10 seconds and a silent session is dropped after about a minute. The remote capture is stopped after five minutes; a command stuck in uninterruptible I/O, such as on a hard NFS mount, can still hold the session open, so interrupt a run that outlasts that.

An archive is accepted only when the remote capture, encryption, authenticated decryption of the whole file and the tar listing all succeed and every required member is present; it is then published under its final name, never replacing an existing file, and its SHA-256 digest is printed. Any failing exit status, including tar's file-changed warning status, exits nonzero without publishing that node's archive, removes the run's in-progress ciphertext and stops the run. Archives already printed by that run remain valid. The recovery directory is made `0700` and new archives are `0600`; earlier archives keep their original modes. [`test-pve-capture.sh`](../scripts/ci/test-pve-capture.sh) checks this behavior offline with synthetic nodes and a throwaway key.

The latest verified three-node capture is `20261115T031032Z` (2026-11-14 local / November 15 UTC), from [#1515](https://github.com/kelchm/home-lab/pull/1515). All three archives passed full authenticated decryption, required-member checks and ciphertext SHA-256 verification. Their SQLite snapshots passed integrity checks, and the captures contain VM definitions 103, 104, 200 and template 9000, the Homepage user, backup webhook/matcher and the local storage's `snippets` flag. Node 1's current and historical router snippets and node 2's router snippet matched the live file hashes; node 3 had no snippet directory. Quorum and guest placement/states were unchanged afterwards. Private verification receipts sit beside the archives under `.private/pve-sbx/recovery/verification-20261115/`; the original `20260827T200605Z` captures are retained.

Capture after material cluster or host configuration changes, including guest definitions, node-local snippets, storage/network settings, users/credentials and notification targets, and record the accepted timestamp here as part of completing that change. This is a manual recovery step; no weekly scheduler is installed. These are off-node configuration archives on the admin workstation, not guest-disk backups, a tested host rebuild or off-site protection. Copy the encrypted artifacts to a second protected location if workstation loss is in scope.

## Node maintenance

PVE hosts are mutable Debian systems patched deliberately, one node at a time; no window is scheduled and nothing reboots automatically. Stay on `pve-no-subscription` and the default kernel; do not enable `pvetest` or test kernels. A major PVE release waits for its first point release and is its own reviewed change. For each node in turn, never two at once:

1. Run the [routine checks](#routine-checks), including both Corosync links, the kernel log, thin-pool data and metadata use, and the previous night's backups.
2. Confirm the other two nodes have memory and CPU headroom, then migrate or shut down the node's guests. Keep one of VMs 103 and 104 running: they are the only routed remote-access path.
3. Run `apt update`, review the change list, then `apt full-upgrade`. Reboot for kernel, microcode, systemd, QEMU or PVE stack changes, or when unsure.
4. Before touching the next node, require the expected kernel and PVE versions, quorum, both links, `pvesm status`, storage mounts, networking and healthy guests.

Stop on the first unexplained regression rather than upgrading around a failed node. Do not enable PVE's dynamic load balancer; evacuation stays explicit.

## Design decisions and deferrals

These choices carry forward from the original [cluster plan](../docs/plans/20260814-pve-cluster.md), which keeps the full rationale.

- **Storage.** Each node uses the installer-default ext4 root and node-local LVM-thin `local-lvm` for guest disks. ZFS is not used on these SN770s because of public controller-drop reports under ZFS workloads ([SN770 evaluation](../docs/sn770-zfs-qualification.md)); Ceph does not fit single-NVMe, 32 GB, 2.5 GbE nodes. `local-lvm` is not shared: after an abrupt node loss, a guest returns by repairing the node or restoring from `backups-pve-sbx`, not by HA restart. Snapshots are not backups.
- **Capacity.** Keep at least 6 GiB per host for PVE, migration and transient load. For every running guest to survive a planned one-node evacuation, keep total running guest memory at or below about 52 GiB, placed so the destination has room. `PVEStorageSpaceLow` warns when a PVE storage is over 90% full for 30 minutes; thin-pool metadata has no alert, so read it with `lvs` in the routine checks.
- **Guests.** Use VMIDs 100–199 for platform and recovery-critical guests, 200–299 for persistent services, 300–399 for lab and test, 400–499 for disposable guests and 9000–9099 for cloud-init templates, which are never booted. Attach guest NICs to `vmbr0` with an explicit tag, VLAN 21 by default; never untagged. VLAN 19 is only for remote-access routing peers; VLAN 10 or 90 is a deliberate zone placement; VLAN 25 is only for a storage-attached guest registered in the [storage VLAN registry](../docs/architecture.md#storage-vlan-registry) with an explicit per-IP NAS export ACL. New VMs default to VirtIO SCSI single with discard and SSD emulation, the QEMU guest agent, a serial console, and CPU type `x86-64-v2-AES` unless pinned to one node. Prefer VMs; use unprivileged LXC only for small services that tolerate host-kernel coupling.
- **Access and firewall.** UniFi enforces inter-VLAN policy. Do not enable a cluster-wide PVE firewall until local console recovery is proven on all three nodes. Any future Kanidm OIDC login must not become the only way into PVE; `root@pam` remains local-console break glass.

| Deferred | Revisit when |
|---|---|
| Shared NFS guest tier and PVE HA | A guest needs HA restart more than independence from NAS and VLAN 25 failure; benchmark it, then prove power-loss failover before calling anything HA. Before enabling HA resources, which arm the watchdog, also confirm Corosync link 0 stays clean while the 2.5 GbE carries iperf3 plus a `vzdump` run, and that a node keeps quorum over link 1 with its 1 GbE pulled. Run those checks sooner if routine checks show link 0 faults during backups. These stress and cable-pull results remain unverified; quorum loss can still disrupt management without HA |
| PVE SDN zones and VNets | Keeping the per-node `bridge-vids` set and the UniFi profile in step becomes a repeated management problem |
| Proxmox Backup Server | Full `vzdump` backups or restores no longer meet space or recovery-time needs; it would run on independent hardware at the reserved `pbs` addresses |
| Proxmox Datacenter Manager | A second PVE or PBS system exists, or a central status view saves real time |
| Ansible for hosts | Rebuilds take too many error-prone steps, host drift appears, a fourth node arrives, or the same change keeps crossing every node |
| Local ZFS and PVE replication | The SN770s are replaced by drives that pass sustained ZFS scrub and send burn-in on these hosts |
| Thin-pool metadata alert | Thin-pool data use passes 70% or snapshots enter routine use |
| LXC restore drill | The first LXC guest is created |
| UPS-aware shutdown and notification | The PVE nodes gain a UPS |
| More guest VLANs, 10 GbE, Ceph | Trust classes outgrow VLAN 21 and guest firewalls; measured migration or backup windows justify faster links; different hardware provides dedicated OSDs, more RAM and 10+ GbE |

## Outstanding commissioning gates

None of these has a recorded result since the 2026-08-27 commissioning record above; no record is not a pass. Retiring the historical plan does not clear these gates or qualify PVE for irreplaceable data.

- Repeat the idle and I/O acceptance window on every host under the firmware-level PCIe ASPM disable, leaving NVMe APST unchanged: [#1507](https://github.com/kelchm/home-lab/issues/1507). Node 3's earlier clean 100-cycle screen preceded two later errors and is not sufficient evidence for the gate.
- Repeat a timed isolated restore after the storage requalification and record restore RTO and throughput: [#1513](https://github.com/kelchm/home-lab/issues/1513), held on #1507.
- Complete physical cold-power removal and recovery on all three nodes, then remove the obsolete SanDisk qualification USB from node 1 once its NVMe cold-power boot is proven: [#1508](https://github.com/kelchm/home-lab/issues/1508). GLKVM cannot assert ATX power.
- Enroll TOTP for `kelchm@pve`: [#1509](https://github.com/kelchm/home-lab/issues/1509).
- Configure and test an authenticated SMTP notification target; direct-to-MX Postfix delivery from the home public IP is rejected by Gmail: [#1510](https://github.com/kelchm/home-lab/issues/1510).

Installation, BIOS, KVM, PXE, node acceptance, cluster join, and recovery-console procedures live in [the PVE node bootstrap runbook](../docs/runbooks/pve-node-bootstrap.md). The superseded [cluster plan](../docs/plans/20260814-pve-cluster.md) retains the original design rationale and the 2026-08-27 acceptance record.
