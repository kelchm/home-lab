# SN770 ZFS qualification — session handoff (2026-08-18)

## Late-session authoritative update

### 2026-08-21 4 KiB logical-sector reproduction update

This subsection supersedes the earlier current-state, reproduction-priority, and firmware-claim descriptions below. Testing occurred on 2026-08-21 EDT / 2026-08-22 UTC.

- Follow-up research found that the canonical OpenZFS `zfs send` failure used SN770 namespaces formatted with 4096-byte logical sectors. Two independent first-hand reports described the same `CSTS=0xffffffff` failure at 4096 bytes and successful operation after returning the same hardware to 512 bytes. The earlier qualification procedure deliberately held the namespace at 512 bytes, so the clean HMB matrix did not test this stronger reported trigger.
- The revised procedure now contains an isolated section 4A reproduction arm. `scripts/sn770-send-screen.sh` gained an optional `--logical-block-size` guard and records both expected and observed logical block size in every manifest and final evidence capture.
- Before the destructive change, the identity gate passed for HP serial `8CG7466C9K`, SN770 serial `23030W800174`, and firmware `731100WD`. The healthy 512-byte state was sealed on Athena, then only pool `sn770test` was destroyed. LBA format 1 was confirmed as 4096 bytes before `nvme format`; the namespace and controller serial were revalidated after its reset.
- The 4 KiB pool was recreated with `ashift=12`, compression disabled, and the same 700 GiB incompressible seed. The seed completed without error in 17m02s at 701 MiB/s average. No unmonitored setup scrub was run, preserving `zfs send` as the first controlled reproducer.
- The historical arm again used kernel `6.8.12-17-pve`, OpenZFS `2.2.8-pve1`, 4096-byte logical sectors, HMB `HSIZE: 8192` (32 MiB), and `HMDLEC: 8`. The warmed drive completed one 700 GiB send in 5m47s at a 68 C / 87 C peak, one full scrub in 3m42s at 74 C / 94 C, and one concurrent send plus scrub in 7m38s at 74 C / 94 C. The scrub and mixed scrub each repaired `0B` and reported zero errors.
- Complete local and independently streamed off-host kernel logs contain no NVMe timeout/reset, `CSTS=0xffffffff`, namespace change, PCIe/AER event, SMART critical warning, media/error-log increment, ZFS error, or pool degradation. All three arms passed the runner's explicit 80 C composite / 103 C maximum-sensor guards.
- Evidence is sealed on Athena under `~/sn770-results/pve-lab-1/731100WD/fourk-lba/`:

  | Evidence directory | Result | `SHA256SUMS` hash |
  |---|---|---|
  | `preformat-512-20260821T235207Z` | pre-format 512-byte baseline, clean | `6c92239a7d6c82baeb7d433635a8cbca7bd4ecdc29a7a94604e6bc118c5db684` |
  | `send-only-20260822T001512Z` | one complete 4 KiB send, clean | `c959c96e7aba9aecbd9138118e249da29cf75e2dab986dbda1958fc52befcd0e` |
  | `scrub-only-20260822T002208Z` | one complete 4 KiB scrub, clean | `db26585cbbd5fbe63b397abcd41c8d0f43599bf8ecdf304e734ed197f0612bc4` |
  | `mixed-20260822T002652Z` | one complete 4 KiB mixed cycle, clean | `2a1ab261c22cbd4910ef29f0b734b19162def4fdf9864b1fd12dca846aeb80cf` |
  | `restored-modern-20260822T003556Z` | historical final plus modern-kernel restoration, clean | `e1d21d2eba21854ccab4b63ffb148620cdaa53c2ae184971f8c876e1e25e3044` |

- The node is restored to kernel `7.0.14-12-pve`, HMB `HSIZE: 32768` (128 MiB) / `HMDLEC: 1`, with an empty GRUB environment and no workload. The 4 KiB `sn770test` pool remains ONLINE and healthy to preserve the new reproduction condition for follow-up; SMART media/error-log counters remain zero.
- Classification: 4 KiB logical sectors are not sufficient to reproduce the fast controller-drop failure on this 1 TB specimen and PCIe 3.0 single-drive host. Together with the earlier 512-byte historical-HMB series, this substantially weakens both “eight HMB descriptors are the trigger” and “4 KiB plus ZFS send is universally sufficient.” The highest-information next discriminator is a second 1 TB `731100WD` specimen at 4 KiB on this host or a closer multi-drive/high-queue topology, not another short repeat on this serial.
- Firmware correction: SanDisk's published HMB advisory covers Windows 11 24H2, the 2 TB SN770, and firmware `731130WD`. `731150WD` exists for the 1 TB SN770, but no authoritative public changelog was found tying it to this Linux/ZFS failure. Do not describe it as a vendor-confirmed Linux HMB fix or perform a firmware A/B until a failing baseline is available.

### 2026-08-20 proper-heatsink qualification update

This subsection supersedes the earlier thermal limitation and current-state
descriptions below. Testing occurred on 2026-08-20 EDT / 2026-08-21 UTC.

- The identity gate passed before every new workload: HP serial `8CG7466C9K`,
  WD_BLACK SN770 1TB serial `23030W800174`, and firmware `731100WD`. The
  historical arm used kernel `6.8.12-17-pve`, OpenZFS `2.2.8-pve1`, HMB
  `HSIZE: 8192` (32 MiB), and `HMDLEC: 8`.
- A proper NVMe heatsink replaced the earlier temporary cooling. With no other
  test-condition change, a full scrub completed in 3m41s at a 76 C composite /
  95 C maximum-sensor peak, repaired `0B`, and reported zero errors. A full
  mixed send + scrub then completed in 7m40s at 74 C / 94 C, also clean.
- The runner used its normal 80 C composite guard but an explicit
  `--sensor-stop 103` override for these runs. The script's general-purpose
  default remains 90 C. The tracked maximum was NVMe Sensor 1; its physical
  location is vendor-specific, so this document calls it the maximum sensor or
  hotspot rather than asserting that it is a particular component.
- The cooled qualification series then completed four more back-to-back mixed
  send + scrub cycles, each in 7m40s, at a 74 C / 94 C series peak. Five
  additional back-to-back sends each completed in 5m46s--5m47s at a 70 C /
  88 C series peak. Together with the first mixed run, this is ten complete
  sends and five complete concurrent scrubs under the historical eight-segment
  geometry. The separate scrub-only screen is an additional complete scrub.
- Across the cooled runs there was no SMART critical warning, media or error-log
  entry, NVMe reset/timeout, `CSTS=0xffffffff`, namespace loss, PCIe/AER error,
  ZFS error, or pool degradation. The proper heatsink changed the result from a
  thermal-guard stop after about 70 seconds to sustained clean workloads without
  an increasing heat-soak trend.
- Evidence is sealed on Athena under
  `~/sn770-results/pve-lab-1/731100WD/historical-hmb-8seg/`:

  | Evidence directory | Result | `SHA256SUMS` hash |
  |---|---|---|
  | `heatsink-scrub-20260821T012406Z` | one complete scrub, clean | `b4d10bffdff6ef1c334d16055de8900b071ca8d13c0a91d5e9a03165a347a4ae` |
  | `heatsink-mixed-20260821T013109Z` | one complete mixed cycle, clean | `e1faa4442bacb8eec62cfefcbbaf26109e6c36fc9862762421313dccd71195c3` |
  | `heatsink-mixed4-20260821T014056Z` | four complete mixed cycles, clean | `a3a7066fd8d12c21813a3b669cee0fcf45024fe532ed89ab8c12cb4d6ea0e52c` |
  | `heatsink-send5-20260821T024147Z` | five complete sends, clean | `db5a51105d903a882287a708fd3340650bf3adf9d8a5bb7c79a2b33346cab5d5` |
  | `heatsink-restored-modern-20260821T031309Z` | modern-kernel restoration, clean | `b7600d093c30ac6709db76f9eca3ac685fd5cf01e9752e2e760970fd1c879c3a` |

- The node was restored to kernel `7.0.14-12-pve`. The live HMB is again
  `HSIZE: 32768` (128 MiB) with `HMDLEC: 1`; the GRUB environment is empty,
  `sn770test` is healthy, no workload is active, and SMART media/error-log
  counters remain zero.
- This is a strong negative reproduction result for the reported historical HMB
  geometry under the bounded qualification workload. It is not a controller-drop
  reproduction, a firmware A/B, or the final multi-day/three-drive qualification.

### 2026-08-19 historical-allocator update

This subsection supersedes the earlier modern-kernel state below. As of
2026-08-19 00:24 EDT:

- A read-only netboot of SystemRescue 12.03 (`6.12.61-1-lts`) on the exact node
  and drive proved the missing historical condition. `nvme get-feature
  /dev/nvme0 -f 0x0d -H` reported HMB enabled, `HSIZE: 8192` (32 MiB), and
  `HMDLEC: 8`. This is the actual eight-descriptor geometry; `hmmaxd=8` alone
  would only have described the controller maximum.
- The read-only discovery boot confirmed HP serial `8CG7466C9K`, SN770 serial
  `23030W800174`, firmware `731100WD`, zero SMART/media/error-log errors, and no
  NVMe-backed mount or swap. Its sealed evidence is
  `historical-hmb-6.12/read-only-20260819T040752Z`; that directory's
  `SHA256SUMS` hashes to
  `faa86c98b8f98fd5039ad25872289e8eda9cbb24875cf30034681d9eb4be2034`.
- The signed PVE 8 kernel package `proxmox-kernel-6.8.12-17-pve-signed` was
  verified against its official SHA-256
  `147f1050dc776e40d83baea043063932ec865524827486d2f7c3756871ee2e22`
  and installed alongside, not instead of, the normal PVE 9 kernel on the
  expendable SanDisk USB. The temporary GRUB `next_entry` was explicitly
  cleared after boot; the normal `7.0.14-12-pve` entry remains the recovery
  default.
- The live qualification environment is now `6.8.12-17-pve`, HMB 32 MiB / eight
  descriptors, with in-kernel OpenZFS `2.2.8-pve1`. Matching PVE 8 ZFS commands
  and libraries were checksum-verified and extracted under
  `/opt/zfs-2.2.8-pve1`; the system packages were not downgraded or mixed with a
  Bookworm repository.
- One complete historical-geometry 700 GiB `zfs send` finished in 5m48s with a
  76 C composite / 99 C sensor peak. There were no controller resets, timeouts,
  PCIe/AER errors, SMART warnings, media errors, error-log entries, namespace
  changes, or ZFS errors. Evidence is sealed at
  `historical-hmb-8seg/send-only-20260819T0430Z`; its `SHA256SUMS` hashes to
  `e529fab3af5b01c1f5c312137f5e202a240ce0aaabdbc297868c773f4407bdc8`.
- A historical-geometry scrub-only screen issued roughly 200 GiB before its
  automatic stop after 73 seconds at 80 C / 103 C. A final mixed `zfs send` +
  scrub screen ran concurrently for 69 seconds before the same stop. Both kept
  `HMDLEC: 8`, canceled the scrub, terminated the send, left the pool ONLINE,
  and recorded zero controller, SMART, media, namespace, or ZFS errors. Evidence
  is sealed at `historical-hmb-8seg/scrub-only-20260819T0429Z` and
  `historical-hmb-8seg/mixed-20260819T0432Z`; their `SHA256SUMS` hashes are
  `285eea9b38a07b925549ced2cfb89fd866a0972a52f15d1e6a2795685285d070`
  and `c0caf0bd6b014649e2bb891bdd471c389413c665e89a02bf3de3efa0ac70b153`.
- The node was returned to its normal `7.0.14-12-pve` kernel. The live feature
  now reports `HSIZE: 32768` (128 MiB) and `HMDLEC: 1`; GRUB has no lingering
  `next_entry`, the pool is healthy, no workload remains, and SMART/media/error
  counts are zero. Restoration evidence is at
  `historical-hmb-8seg/restored-modern-20260819T043510Z`; its `SHA256SUMS`
  hashes to `a220bbc3e9a6c895fc90090f423a18d7b76534a9714db005a96248ada5d6b250`.
- No controller-drop reproduction occurred inside the safe cooling envelope.
  A full historical scrub or sustained mixed run now requires materially better
  cooling; the current setup supports full sends but reaches the guard too soon
  for either heavier workload.

### Earlier 2026-08-18 modern-kernel matrix

The state below is retained for chronology. As of 2026-08-18 23:43 EDT:

- `pve-lab-1` runs PVE kernel `7.0.14-12-pve` from the authorized SanDisk USB.
- The mandatory identity gate passed: HP serial `8CG7466C9K`; WD_BLACK SN770 1TB
  serial `23030W800174`; firmware `731100WD`.
- The SN770 was intentionally wiped and now holds the disposable whole-disk ZFS
  pool `sn770test`. Dataset `sn770test/payload` contains a 700 GiB incompressible
  seed and snapshot `sn770test/payload@seed`.
- The live kernel default is 128 MiB HMB in one segment. This supersedes the stale
  32 MiB statement later in this document.
- The first scrub was stopped at 31.79% after SMART critical warning `0x2` at
  84 C composite / 107 C sensor 1. The controller and namespace remained present,
  the pool stayed ONLINE, and no NVMe or media errors were recorded. This was a
  thermal stop, not an SN770 controller-drop reproduction.
- Additional physical cooling was added. It reduced the same point in a scrub by
  approximately 6 C, but a corrected scrub-only screen still reached the
  conservative 80 C composite guard at roughly 40--45% completion. It was canceled
  automatically at 80 C / 103 C, before a SMART warning. The pool remained ONLINE
  and error-free. The added cooling is sufficient for full send-only discovery
  runs, not uninterrupted scrubs or concurrent qualification.
- Five full 700 GiB `zfs send` runs to `/dev/null` completed on firmware `731100WD`
  without any NVMe reset/timeout, `CSTS=0xffffffff`, PCIe/AER, SMART/media,
  namespace, ZFS, or pool-health failure:

  | Modern HMB condition | Sends | Duration | Peak temperature | Result |
  |---|---:|---:|---:|---|
  | 128 MiB / one segment | 2 | 5m28s, 5m26s | 78 C / 101 C | clean |
  | 32 MiB / one segment | 1 | 5m26s | 78 C / 101 C | clean |
  | 200 MiB / one segment | 1 | 5m28s | 78 C / 101 C | clean |
  | HMB disabled | 1 | 5m30s | 77 C / 100 C | clean |

- This is a negative reproduction result for total HMB size on the modern
  single-segment allocator. It does not test the historical 32 MiB allocation as
  eight 4 MiB descriptors. That remains the highest-value next reproduction arm.
- Off-host run evidence and per-directory `SHA256SUMS` files are sealed under
  `~/sn770-results/pve-lab-1/731100WD/` on Athena. Important evidence directories:
  `hmb-128/send-only-20260819T0245Z`,
  `hmb-128/send-only-20260819T0256Z`,
  `hmb-128/scrub-only-20260819T0312Z`,
  `hmb-32/send-only-20260819T0318Z`,
  `hmb-200/send-only-20260819T0327Z`, and
  `hmb-0/send-only-20260819T0335Z`.
- The first scrub-runner attempt at `hmb-128/scrub-only-20260819T0306Z` is explicitly
  labeled `classification=harness_error_not_drive_failure`; its mistakenly
  unmonitored scrub was manually canceled at 1.90% with no drive or pool error.
- The bounded runner is installed on the host as
  `/usr/local/sbin/sn770-send-screen.sh`; its Git source is
  `scripts/sn770-send-screen.sh`. `scripts/set-sn770-hmb-cap.sh` performs the
  identity-gated GRUB changes. Both send and scrub modes stop on thermal or
  controller failure and are observed from Athena.
- After the matrix, GRUB was restored byte-for-byte to its pre-matrix configuration
  and the node rebooted. Current state is the implicit default 128 MiB / one segment;
  `sn770test` is ONLINE, firmware is still `731100WD`, and SMART media/error-log
  counts remain zero. Final cleanup evidence is at
  `hmb-128/restored-default-20260819T0341Z`; its `SHA256SUMS` hashes to
  `2eadcc2412597e3c535f386907d948d48db72d2f11effdf12d974c821072e58e`.
- No controller-drop reproduction has occurred yet. These successful short screens
  are meaningful negatives for the reported fast-failure cases, not qualification
  passes.

**Status:** In progress. The Synology netboot.xyz cutover is complete. Node `pve-lab-1` boots the expendable PVE installation on the SanDisk USB and is reachable at `10.32.20.11`. The SN770 contains the disposable 4 KiB whole-disk pool `sn770test`; the 512-byte historical-geometry series and the isolated 4 KiB reproduction arm are both complete and clean. The node is restored to its normal modern kernel. A second-specimen/topology discriminator, any justified firmware A/B, and the longer multi-drive qualification remain.

This document is the source of truth for resuming. It captures the corrected goal,
everything built, everything learned, exact paths/credentials, current state, open
problems, and next steps.

---

## 1. The actual goal (corrected mid-session)

This is **not** the "install a PVE cluster" task from
[`docs/plans/20260814-pve-cluster.md`](20260814-pve-cluster.md). It is a **controlled
ZFS qualification experiment** on the WD_BLACK SN770 1TB drives:

1. On **current firmware (`731100WD`)**, attempt to reproduce the documented SN770 controller drop-off under sustained ZFS scrub + `zfs send`, isolating HMB size/descriptor geometry and the independently reported 4 KiB namespace-format trigger.
2. Establish a repeatable failing baseline before treating any firmware update as a fix test. The currently published 1 TB update is `731150WD`, but no authoritative vendor changelog tying it to this Linux/ZFS failure has been found.
3. If a failing baseline is established, update one drive through the official Windows SanDisk Dashboard and rerun the identical condition, with a second drive held on old firmware as a control.

**The authoritative procedure is the 13-section test plan the user pasted this
session** ("SN770 ZFS qualification procedure"). It supersedes my earlier simplified
approach. Key rules from it that override earlier decisions:

- OS runs on a **separate boot disk** so host + logs survive an NVMe controller drop;
  the SN770 stays **entirely raw** as the test device.
- Baseline + all runs must use the **intended PVE kernel** (HMB allocation is
  kernel-specific), i.e. **install PVE on the separate disk**.
- **Do NOT use the gist `nvme fw-download` Linux firmware method for the qualification
  result** — the plan requires the official **SanDisk Dashboard on Windows**, drive
  natively in an M.2 slot. (The gist method + staged `.fluf` below are exploratory
  only, not the qualification path.)
- Collect `731100WD` baselines on **all three** nodes before any upgrade; hold
  `pve-lab-2` on old firmware as a control.
- Precise failure definition and per-condition durations (48h+/10 sends/5 scrubs;
  final qual 7 days × 3 drives). This is a **days-to-weeks** experiment.

---

## 2. Hardware / topology facts learned

- **pve-lab-1** = HP EliteDesk 800 G3 DM 35W, i5-6500T (4c/4t), 32 GB RAM,
  BIOS **P21 Ver. 02.45 (12/15/2022)**, serial `8CG7466C9K`, UUID
  `A85D9B2D-FC99-65DE-5AAD-DEFF71F8ED3D`.
- NVMe: **WD_BLACK SN770 1TB**, serial `23030W800174`, at PCI `0000:02:00.0`,
  firmware **`731100WD`**. It now contains the disposable whole-disk ZFS pool
  `sn770test`; the earlier PVE partitions were intentionally destroyed after the
  identity-gated baseline.
- **No SATA disk** — confirmed twice (`ata2: SATA link down (SStatus 4 SControl 300)`).
  This is why the OS boot disk is **USB**. The 28.7 GiB removable **SanDisk Ultra
  USB 3.0** is `/dev/sda` and now contains the expendable PVE installation. The
  SN770 remains `/dev/nvme0n1` and is never an eligible OS-install target.
- Onboard NIC: **Intel I219-LM** 1GbE, Linux `eno1`, MAC `ac:e2:d3:0b:dc:0d`,
  on **VLAN 20**. This is the interface that PXE-boots and reaches the GLKVM.
- Second NIC: **RTL8125** 2.5GbE, Linux `enp1s0`, PCI `0000:01:00.0`, on VLAN 25
  (storage). r8169 driver, negotiates 2.5 Gb/s.
- The normal PVE kernel `7.0.14-12-pve` allocates 128 MiB HMB in one descriptor.
  The installed historical kernel `6.8.12-17-pve` allocates 32 MiB across eight
  descriptors, reproducing the historical allocator geometry needed by the test.
- Old stale IP `192.168.1.80` seen on the old-PVE console is a **leftover static
  config** in that install's `/etc/network/interfaces`, not the real network. The
  switch port is native **VLAN 20**; PXE/DHCP correctly yields `10.32.20.x`.

### KVM / control path
- **GL-RM1PE GLKVM** at `10.32.20.10` (root SSH, VLAN 20). This is the only control
  path to node 1: HDMI capture + emulated USB HID keyboard.
- Console screen grabs come from `/run/kvmd/ustreamer.sock`. The Git-owned
  [`devices/glkvm/capture-screen.sh`](../../devices/glkvm/capture-screen.sh)
  copies a JPEG to the operator workstation. `ustreamer` is demand-started by
  an authenticated KVM viewer and stops roughly ten seconds after the last
  viewer disconnects; a stale socket that refuses connections does not mean
  the HDMI or HID path is broken. Open and retain a viewer before unattended
  captures.
- Keyboard control writes raw HID reports to `/dev/hidg0`. Git-owned helpers are
  installed persistently as `/etc/kvmd/user/bin/hid-type` (named keys,
  `type:LITERAL`, `sleep:N`, `ctrl-alt-del`) and
  `/etc/kvmd/user/bin/hid-hold-key KEYHEX SECONDS` (holds a key through POST;
  F9=0x42, F10=0x43, Esc=0x29, F12=0x45). Their source, deployment procedure,
  firmware-update recovery checklist, and smoke tests live in
  [`devices/glkvm/`](../../devices/glkvm/).
- The node reliably lands in the **HP Startup Menu** by holding **Esc** during POST
  (`hid-hold-key.py 0x29 30`). From there: **F12 = Network (PXE) Boot** → then select
  **IPv4** (down, enter). Also F10 = BIOS Setup, F9 = one-time boot menu.
- **HP quirk:** F9/F10/F12 one-shot key *presses* during POST are unreliable (often
  swallowed); the reliable technique is `hid-hold-key.py` to hold the key continuously
  through the POST window, OR reach the Startup Menu via Esc. GRUB's "Reboot Into
  Firmware Interface" is another reliable way into the Startup Menu.
- **Composite HID+MSD BIOS bug (from the device handoff):** with the GLKVM virtual
  media (MSD) enabled, the HP's BIOS keyboard dies. We are **not** using virtual media
  now (netboot instead), so the keyboard works throughout. Virtual media was also a
  dead end for a separate reason (below).

### Why virtual media (GLKVM emulated CD/USB) does not work here
- The GL-RM1PE plugs into the KVM switch's **KCEVE KC-KVM801** black **"Hotkey"
  (keyboard) port**, which is **USB 2.0 full-speed (USB 1.1 speed)**. At full-speed
  the composite HID+MSD device **reset-loops** during enumeration, so the BIOS can't
  read the virtual CD and falls through to the internal disk. Proven with both CD-ROM
  and disk gadget modes, warm and cold.
- The switch's blue **"USB 3.0" ports** are high-speed, but the RM1PE has a **single
  UDC** (one device output) carrying the composite HID+MSD, so you can't split HID
  (Hotkey port) from MSD (USB 3.0 port) across two cables without external CH9329
  hardware. Moving the single cable to a USB 3.0 port would fix speed but break the
  `Ctrl-Ctrl-N` switch hotkey (`devices/glkvm/kvm-switch.py`).
- Conclusion: **netboot over the NIC** is the right path — it leaves the RM1PE on the
  Hotkey port (switching intact), needs no cable moves, and (with no MSD) keeps the
  BIOS keyboard working.

---

## 3. Netboot infrastructure

### Synology netboot.xyz successor (Athena)

A pinned netboot.xyz Compose project now runs on Athena (`10.32.20.5`) from
`/volume1/docker/netbootxyz/compose.yaml`. The Git-owned definition and operations
runbook are in [`synology/netbootxyz/`](../../synology/netbootxyz/).

- Compose project: `netbootxyz-nas`
- Image: `ghcr.io/netbootxyz/netbootxyz:0.7.6-nbxyz24` pinned by digest
- Menu version: `3.0.2`
- Ports: TFTP `10.32.20.5:69/udp`, admin UI `:3000/tcp`, assets `:8080/tcp`
- Persistent state: `/volume1/docker/netbootxyz/config` and `assets`
- Verified: container healthy with zero restarts; both HTTP endpoints return 200;
  a 311,296-byte TFTP transfer of `netboot.xyz-snponly.efi` matched SHA-256
  `1fbcf9f09266bf9e0de2652980f5bd75c16d734400acf8b5e30e272e89b02525`;
  the same checks passed after a container restart.
- SystemRescue 13.00's four assets are cached under
  `/volume1/docker/netbootxyz/assets/asset-mirror/releases/download/13.00-d20a63ac/`.
  Git-owned `local-vars.ipxe` sets `live_endpoint` to
  `http://10.32.20.5:8080`; the deploy runbook normalizes its service UID/mode so
  dnsmasq can serve it. A direct TFTP fetch matched the committed file.
- Two boots reached a usable SystemRescue console and SSH session. The first used
  the upstream GitHub mirror at about 5 MB/s. The second visibly loaded `vmlinuz`,
  `initrd`, `archiso_pxe_http`, and the 1.02 GiB `airootfs.sfs` from Athena; the
  large image transferred at about **110 MB/s**.

Athena had a stopped 2024 Dockge attempt using netboot.xyz 0.7.3/menu 2.0.83. It
contained no meaningful customization and only a stale 1.4 GB Proxmox 8.2-2
asset cache. Its container, network, image, and
`/volume1/docker/dockerge/netbootxyz` data directory were removed on 2026-08-18
to avoid ambiguous ownership and port conflicts.

UniFi VLAN 20 Network Boot now points to Athena (`10.32.20.5`, filename
`netboot.xyz-snponly.efi`).

### Retired temporary GLKVM service (historical reference)

The self-contained PXE/HTTP fallback was removed after the two-boot acceptance
test. Process PID 2777 was stopped, and these exact paths were deleted on
2026-08-18: `/userdata/media/netboot`, `/userdata/media/sysresccd`, and
`/userdata/media/boot.ipxe` (about 1.3 GiB total). The deletion is not locally
recoverable, but all boot content is reconstructable from Git/upstream. Keep the
following details only as implementation archaeology; none of these endpoints or
paths should be expected to exist.

### Netboot server
- Script was `/userdata/media/netboot/netboot-server.py`; an operator-side
  temporary copy was `/tmp/netboot-server.py`. It ran **HTTP on :8080** serving `/userdata/media`, and a
  minimal read-only **TFTP on :69** serving `/userdata/media/netboot/tftp`.
- It was launched detached with `setsid`; PID 2777 was the final process and was
  stopped during cleanup.
- **Trap:** don't `pkill -f netboot-server.py` from an SSH one-liner whose own command
  text contains that string — it matches and kills the SSH shell. Use `pgrep`/explicit
  PID.
- Removal is complete; do not recreate it unless Athena is unavailable and the
  documented rollback is explicitly chosen.

### iPXE binary
- Custom `ipxe.efi` (snponly.efi target, **embedded script**) at
  `/userdata/media/netboot/tftp/ipxe.efi` (301568 bytes). Built via Docker
  (`debian:bookworm`, `--platform linux/amd64`) on this Mac at `~/ipxe-build/`.
  Emulated amd64 gcc **segfaults intermittently** (qemu-on-Apple-Silicon); the build
  is resumable, so a retry loop of `make -j1 ... EMBED=/work/chain.ipxe` eventually
  completes. Embedded script (`~/ipxe-build/chain.ipxe`) does `dhcp` then
  `chain http://10.32.20.10:8080/boot.ipxe`.
- Also `/userdata/media/netboot/tftp/autoexec.ipxe` (copy of boot.ipxe) — iPXE's
  default fallback also finds this if the embed path fails.

### UniFi (done by user)
- The temporary DHCP setting was next-server/TFTP `10.32.20.10`, filename
  `ipxe.efi`; it has been replaced by the Athena setting above.

### Historical GLKVM netboot chain (retired)
1. HP firmware PXE (IPv4, via Startup Menu → F12 → IPv4) → DHCP gives node1
   `10.32.20.135`, TFTPs `ipxe.efi` from `10.32.20.10`.
2. `ipxe.efi` embedded script → `dhcp` → `chain http://10.32.20.10:8080/boot.ipxe`.
3. `boot.ipxe` (at HTTP root `/userdata/media/boot.ipxe`) drives the actual boot.

**Gotcha fixed:** `boot.ipxe` must live at the HTTP **root** (`/userdata/media/`), not
under `netboot/`, because the embedded script chains to `http://.../boot.ipxe`.

### SystemRescue kernel/initrd netboot (the size fix)
- `sanboot` of a full ISO **fails** — iPXE UEFI can't open a SAN device that large
  ("Result too large" on the 1.4 GB SR ISO; the 1.7 GB PVE ISO would fail too).
- Fix: **kernel/initrd boot** — iPXE loads `vmlinuz` + `sysresccd.img`, and the
  running initramfs streams the big squashfs over HTTP (`archiso_http_srv=`), so image
  size stops mattering.
- Extracted SR 13.02 netboot files (GLKVM can't loop-mount; used `bsdtar` on this Mac
  against `/tmp/sysrescue.iso`) and uploaded to the GLKVM in archiso layout:
  - `/userdata/media/sysresccd/boot/x86_64/vmlinuz` (16 MB)
  - `/userdata/media/sysresccd/boot/x86_64/sysresccd.img` (**184 MB initramfs**)
  - `/userdata/media/sysresccd/x86_64/airootfs.sfs` (1.15 GB squashfs)
  - `/userdata/media/sysresccd/x86_64/airootfs.sha512`
- The final `/userdata/media/boot.ipxe` was the **diagnostic** version: it loaded the
  kernel, then `initrd ... && echo ">>> INITRD LOADED OK <<<" || echo ">>> INITRD
  FAILED <<<"`, pauses 12s, then boots. Boot params include:
  `archisobasedir=sysresccd archiso_http_srv=http://10.32.20.10:8080/ ip=::::::dhcp
  checksum=1 copytoram=y rootpass=sn770test nofirewall`.
  - `copytoram=y` → SR runs from RAM (survives NVMe drop / network hiccup).
  - `nofirewall` + `rootpass=sn770test` → so I can SSH into the booted SR
    (temp lab password, throwaway node on internal VLAN — rotate/ignore).

### Firmware investigation (EXPLORATORY ONLY — not the qualification path)
- The temporary remote copy of `731150WD.fluf` was deleted with the retired
  GLKVM netboot tree. It had been 2,797,568 bytes, magic `FLUF Format 002`,
  sha256 `d24294c56454ff71af058e1c83c0ae67371207f0f7a7ad40f4cbb41553205367`.
  Do not treat it as staged or available.
- Confirmed procedure (gist + comments): raw `.fluf` fed directly to
  `nvme fw-download /dev/nvme0 -f 731150WD.fluf`, then `nvme fw-commit -s 2 -a 3`
  (the `-s 2 -a 3` is essential or it won't activate), cold reboot. Drive
  signature-validates at commit (bad image fails safe).
- **Per the authoritative plan, do NOT use this for the qualification** — use the
  Windows SanDisk Dashboard. Kept only as a fallback/exploration.
- Local notes: `/tmp/pve1-evidence/firmware-flash-procedure.md`.

### Local artifacts on this Mac (`/tmp/pve1-evidence/`)
- `bios-identity.txt` — recorded identity + applied BIOS deltas + NIC intel.
- `capture-baseline.sh` — generic host baseline capture (superseded by the plan's
  §3/§5 commands; use the plan's).
- `burn-in.sh`, `zfs-repro.sh` — **superseded** by the plan's precise reproducer
  (concurrent repeated `zfs send` + `zpool scrub -w`, plus the HMB matrix). Rewrite to
  match the plan when executing.
- `firmware-flash-procedure.md`, `sysrescue-root-pass.txt`.
- `baseline/pve-lab-1/20260818-systemrescue-6.18.20-1-lts.txt` — 472,227-byte
  read-only discovery capture, sha256
  `66aa0b4210d645fa43334a162007922557429c279fbc972c3f07777123ccbac4`.
- The obsolete `~/ipxe-build/`, `/tmp/netboot-server.py`, `/tmp/sysrescue.iso`,
  `/tmp/sr-extract/`, and `/tmp/kvmgrab.sh` were moved to macOS Trash after the
  repo-backed GLKVM recovery tools were verified. They remain recoverable until
  the user empties Trash.

---

## 4. BIOS state on node 1 (persisted)

Applied from factory defaults during this session:
- VT-x **on**, VT-d **on**.
- After Power Loss = **Power On**.
- Fast Boot **off**; **Network (PXE) Boot on**; Secure Boot **disabled**
  (Legacy off + Secure Boot off; physical-presence code confirmed).
- Wake-on-LAN kept (Boot to Hard Drive); S5 Max Power Savings off.
- UEFI Boot Order: `USB:` then `M.2 SN770` then network entries. Network boot is
  currently reached via the Startup Menu (Esc) → F12 → IPv4, **not** auto-ordered
  first. (Consider setting network first for repeatable netboot, but note that makes
  every boot netboot until changed back.)

---

## 5. CURRENT STATE

- `pve-lab-1` is running PVE kernel `7.0.14-12-pve` from the expendable SanDisk
  USB at `10.32.20.11`. Root SSH accepts the home-lab public key.
- The live identity is HP serial `8CG7466C9K`, WD_BLACK SN770 1TB serial
  `23030W800174`, firmware `731100WD`. The drive contains disposable whole-disk
  pool `sn770test` with the qualification dataset and seed snapshot; the pool is
  healthy and SMART media/error-log counters are zero.
- The normal modern-kernel HMB is active at 128 MiB in one descriptor
  (`HSIZE: 32768`, `HMDLEC: 1`). GRUB has no pending `next_entry`, no workload is
  active, and the historical PVE 8 kernel remains installed as a non-default
  test option on the USB boot disk.
- Athena's `netbootxyz-nas` project is healthy, UniFi points PXE to Athena, and
  two complete boots have passed. The local SystemRescue cache and Git-owned
  endpoint override are active.
- The temporary GLKVM netboot service and its data are gone. GLKVM SSH and screen
  capture remain available; the two general HID helpers now live on the
  persistent user partition and are recoverable from Git after a firmware wipe.
- The USB OS and SN770 pool are temporary qualification state and need not be
  preserved after the experiment. Continue to enforce the exact node and NVMe
  serial gate before any destructive action.

---

## 6. Recurring blockers / traps

- Earlier 1Password SSH-agent locking interrupted GLKVM access, but passwordless
  SSH is working now. Revalidate before relying on it for a multi-day run.
- The GLKVM has no ATX wiring to node 1. A healthy OS can reboot remotely, as the
  second acceptance boot proved, but clearing a hard hang/panic still requires a
  physical power-cycle.
- HID typing at the **iPXE shell** drops characters (typed `chain http://...` came out
  `chae`); the Linux console is more forgiving. Prefer re-triggering PXE over typing at
  the iPXE prompt.
- `hdiutil` won't mount the hybrid SR ISO on macOS ("no mountable file systems"); use
  `bsdtar -xf` (libarchive reads ISO9660).
- Emulated amd64 Docker build of iPXE segfaults intermittently; resume-retry loop works.
- WD firmware host needs `curl -k` from the GLKVM (its CA bundle can't verify WD's cert).

---

## 7. Next steps to resume

1. Inventory a second 1 TB `731100WD` SN770, preferably `pve-lab-2`, before destructive work. Test that second specimen at 4 KiB on this same host if practical; this distinguishes a specimen/lot effect from a platform effect with minimal new variables.
2. If the second specimen is also clean, move to the closer reported topology: multiple old-firmware SN770s, 4 KiB logical sectors, RAIDZ/mirror concurrency, and a higher-queue PCIe 4.0 host. Do not spend a 48-hour continuous 512-byte dwell expecting it to reproduce the canonical fast 4 KiB failure.
3. Treat a 48-hour 512-byte dwell as operational qualification and an idle/APST-transition screen, not as the primary reproduction experiment.
4. Do not update `pve-lab-1` to `731150WD` until a failing baseline exists. If one is established, use the official Windows SanDisk Dashboard, keep another drive on old firmware as a control, and rerun the exact failing condition.
5. Return all drives to 512-byte logical sectors before the final seven-day, three-drive operational qualification in procedure section 12.

---

## 8. Quick reference

| Item | Value |
|---|---|
| GLKVM (control) | `10.32.20.10`, root SSH (VLAN 20) |
| Node 1 PVE IP | `10.32.20.11` (VLAN 20, via `eno1` MAC `ac:e2:d3:0b:dc:0d`) |
| Synology netboot UI | `http://10.32.20.5:3000/` |
| Synology assets / TFTP | `http://10.32.20.5:8080/`; `10.32.20.5:69`, file `netboot.xyz-snponly.efi` |
| SystemRescue 13.00 local assets | `/asset-mirror/releases/download/13.00-d20a63ac/` on Athena |
| Last SR temp login | Expired on reboot; derive a new menu `rootpass=` for each netboot |
| USB OS disk | SanDisk Ultra USB 3.0, 28.7 GiB, normally `/dev/sda`; expendable, but re-resolve before writes |
| Qualification drive | WD_BLACK SN770 1TB, serial `23030W800174`, `/dev/nvme0n1`, currently 4 KiB logical sectors with disposable pool `sn770test` |
| Normal / historical kernels | `7.0.14-12-pve` (128 MiB/one descriptor) / `6.8.12-17-pve` (32 MiB/eight descriptors) |
| Published 1 TB firmware candidate | `731150WD`; use Windows Dashboard only after a failing baseline; no authoritative Linux/ZFS fix changelog found |
| Screen grab | `devices/glkvm/capture-screen.sh /tmp/kvm-snap.jpg` |
| HID input | GLKVM `/etc/kvmd/user/bin/hid-type`, `/etc/kvmd/user/bin/hid-hold-key` |
| Reach Startup Menu | Reboot/CAD, then hold Esc: `hid-hold-key 0x29 30` |
| PXE from Startup Menu | F12, then Down+Enter (IPv4) |
