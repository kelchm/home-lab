# PVE node bootstrap through GLKVM and netboot.xyz

Use this runbook to operate the existing out-of-band console and PXE infrastructure while inventorying or installing the HP EliteDesk PVE nodes. It supplies the control path for phase 1 of the [PVE cluster plan](../plans/20260814-pve-cluster.md); that plan remains authoritative for hardware gates, install choices, addressing, networking, and cluster formation.

## Control path

| Component | Endpoint | Role |
|---|---|---|
| GLKVM PiKVM UI | `https://10.32.20.10/` | HDMI console, USB HID keyboard, downstream KVM selection |
| GLKVM appliance UI | `https://10.32.20.10:8888/` | Firmware and appliance management only |
| netboot.xyz UI | `http://10.32.20.5:3000/` | Menu and local-asset administration |
| netboot.xyz assets | `http://10.32.20.5:8080/` | Locally served installer and live-image payloads |
| netboot.xyz TFTP | `10.32.20.5:69/udp`, `netboot.xyz-snponly.efi` | UEFI PXE bootloader |

GLKVM provides console and keyboard but no ATX control. A responsive OS can reboot through SSH or Ctrl-Alt-Delete; a hard hang or powered-off node requires a physical power action.

Do not use GLKVM virtual media on these hosts. The appliance's composite HID and mass-storage device reset-loops through the downstream KVM's full-speed Hotkey port, which can make the BIOS keyboard unavailable. PXE leaves mass storage disabled, preserves KVM switching, and is the proven installation path.

## Record the PVE KVM mapping first

The committed GLKVM mapping currently records only `k8s-prod-1` through `k8s-prod-3` on downstream ports 4 through 6. The PVE ports are not yet verified. Before sending any HID input:

1. Select one candidate downstream port from the PiKVM **Hosts** menu or with `/etc/kvmd/user/bin/kvm-switch PORT` on GLKVM.
2. Compare the displayed hostname, chassis label, and expected power state. Do not identify a node from an old IP shown by a stale installation.
3. Record the verified `pve-lab-1` through `pve-lab-3` mapping in `devices/glkvm/override.yaml` and its table in `devices/glkvm/README.md`, apply the override, and verify every button once.

Treat an unverified channel as the wrong host. Never launch a destructive installer or firmware tool until the on-screen identity and the private hardware inventory agree.

## Preflight

1. Unlock the operator SSH key provider and verify non-interactive access to GLKVM and Athena before starting a reboot.
2. Open the PiKVM video page and keep it open. GLKVM starts `ustreamer` on demand and stops it shortly after the last authenticated viewer disconnects.
3. Verify the PXE services from a VLAN 20 client:

   ```sh
   tftp 10.32.20.5 -g -r netboot.xyz-snponly.efi
   curl -fsSI http://10.32.20.5:8080/
   ```

4. Confirm UniFi VLAN 20 Network Boot still points to TFTP server `10.32.20.5` with filename `netboot.xyz-snponly.efi`.
5. Verify the intended PVE installer. The pinned netboot.xyz menu release `3.0.2` advertises PVE `9.1-1`, not the planned `9.2-1`. Before commissioning, stage a local `9.2-1` entry containing `vmlinuz`, `initrd`, and `proxmox.iso`, verify the ISO against Proxmox's published checksum, and complete one cold-boot acceptance test. Do not silently substitute the older menu entry.
6. Confirm GLKVM mass storage is disabled and the target host's onboard 1 GbE switch port is an untagged VLAN 20 member.
7. Keep physical power access available. The KVM cannot recover a node that is off or hard-hung.

The repository does not yet contain a tested PVE `9.2-1` custom menu entry. Staging and validating that entry is a commissioning gate, not an instruction to point a production install at an unreviewed development menu.

## Reach the HP PXE menu

One-shot F9, F10, and F12 presses during POST are unreliable on these EliteDesk hosts. Holding Esc through POST and selecting the desired function from the HP Startup Menu is the proven sequence.

1. Select and visually verify the target KVM channel.
2. Reboot a healthy host through its OS. If only HID is available, send Ctrl-Alt-Delete from the PiKVM UI or run:

   ```sh
   ssh root@10.32.20.10 '/etc/kvmd/user/bin/hid-type ctrl-alt-del'
   ```

3. As the reboot begins, hold Esc for 30 seconds:

   ```sh
   ssh root@10.32.20.10 '/etc/kvmd/user/bin/hid-hold-key 0x29 30'
   ```

4. At the HP Startup Menu, choose **F12 Network Boot**, then select **IPv4** with Down and Enter. The paced helper sequence is:

   ```sh
   ssh root@10.32.20.10 '/etc/kvmd/user/bin/hid-type f12 sleep:1 down enter'
   ```

5. Confirm the UEFI client receives a VLAN 20 DHCP lease, downloads `netboot.xyz-snponly.efi` from Athena, and reaches the netboot.xyz menu.

If the host misses the menu, reboot and repeat the held-Esc sequence. Do not type recovery URLs at the iPXE shell unless unavoidable; this HID path has dropped characters there.

## Start the PVE installer

1. In netboot.xyz, select the locally staged and verified **Proxmox VE 9.2-1** entry. Stop if only `9.1-1` is offered.
2. Watch the kernel, initrd, and `proxmox.iso` load from Athena's `10.32.20.5:8080` endpoint. A WAN download or an unexpected asset path fails the local-install acceptance gate.
3. Follow the PVE plan's installation baseline exactly: UEFI, the verified target NVMe, ext4 with installer-default LVM-thin, the final node FQDN and management address, VLAN 20 gateway and DNS, and `America/New_York`.
4. Re-resolve the destination disk from model, serial, capacity, and logical sector size immediately before accepting destructive partitioning. Do not infer it from `/dev/nvme0n1` alone.
5. After installation, put the NVMe PVE boot entry ahead of network boot. PXE remains available through the manual Startup Menu rather than running on every reboot.

## Post-install acceptance

Before moving to the next node:

1. Reboot without intervening at the console and confirm the host boots from its NVMe installation.
2. Verify the expected hostname, management address, PVE UI on TCP 8006, and SSH host key. Replace temporary DHCP or qualification-era known-host entries deliberately.
3. Complete the PVE plan's NIC inventory, RTL8125 link test, storage burn-in, backup/restore, reboot, and cold-power gates.
4. Capture the final console and record the verified downstream KVM port. Keep the viewer open while running:

   ```sh
   devices/glkvm/capture-screen.sh /tmp/pve-bootstrap.jpg
   ```

5. Confirm the node can be recovered through both the PiKVM UI and the held-Esc PXE sequence before depending on remote-only maintenance.

## Troubleshooting

- **Viewer works but scripted capture fails:** keep an authenticated PiKVM video viewer open so `ustreamer` remains running, then retry the capture.
- **Keyboard disappears in BIOS:** ensure GLKVM virtual media/mass storage is disabled, reconnect the viewer if needed, and cold-power-cycle physically if the composite device remains wedged.
- **POST ignores F12:** use held Esc for 30 seconds, then choose F12 from the Startup Menu.
- **iPXE prompt drops characters:** restart the PXE sequence instead of typing a long `chain` command.
- **PXE downloads but PVE is the wrong version:** stop. The pinned menu's stock PVE entry is `9.1-1`; repair or stage the reviewed local `9.2-1` entry before proceeding.
- **No video after a hard failure:** verify the KVM channel and HDMI path, then use physical power. GLKVM cannot assert the host power button.

For appliance deployment and recovery, use [devices/glkvm/README.md](../../devices/glkvm/README.md). For netboot.xyz deployment, assets, verification, and rollback, use [synology/netbootxyz/README.md](../../synology/netbootxyz/README.md).
