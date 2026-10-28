# DGX Spark Bring-up

Current-state record and recovery procedure for the two-node NVIDIA DGX Spark
cluster. This runbook implements phase 3 of the
[network-topology plan](../plans/20260821-network-topology.md#phase-3--dgx-sparks).

**Status (2026-08-27):** LAN, storage VLAN, direct fabric, host anti-transit, raw RDMA, NCCL, and the RTL8127 burn-in are configured and tested. The live No Workloads containment slice is applied and validated; the broader UniFi firewall matrix, Synology export changes, Spark-to-NAS throughput, DNS, and final temporary-access cleanup remain open. The incomplete items are listed explicitly under [Remaining work](#remaining-work).

**Host recovery checks passed (2026-09-30):** after swapping the port 7/8 modules and replugging both cable ends, both links passed 30 minutes idle and a complete C4 250k cold/replay pair at 10GbE with zero new carrier drops. Both inference containers are stopped through SparkRun; management/storage probes and GPU-idle checks pass. The switch SFP fault-history recheck still requires controller sign-in in [#649](https://github.com/kelchm/home-lab/issues/649). Candidate comparisons remain paused, and the fault's cause is unconfirmed.

## Invariants

- The 10GbE NIC is the only default-route interface.
- VLAN 25 is a storage-only L2 leg with no gateway.
- Both ConnectX-7 fabric prefixes exist only as connected routes on the Sparks:
  no gateway, forwarding, bridge, gateway static route, or route advertisement.
- Fabric names exist only in the Sparks' `/etc/hosts`; never publish them in DNS
  or service discovery.
- Workload and service software uses the `10.32.21.x` addresses. Fabric
  addresses are limited to RDMA/NCCL and explicit operator diagnostics.
- MTU is 1500 on the LAN and storage legs, and 9000 only on the fabric.
- Spark-to-NAS bulk traffic must use the VLAN 25 source addresses.
- No private key, login password, or other secret belongs in this repository.

## Inventory and addressing

| Item | Spark 1 | Spark 2 |
|---|---|---|
| Hostname | `spark-1` | `spark-2` |
| GLKVM port | 7 | 8 |
| Core Aggregation port | 7 | 8 |
| RTL8127 interface | `enP7s7` | `enP7s7` |
| Workloads address | `10.32.21.31/24` | `10.32.21.32/24` |
| Default gateway | `10.32.21.1` | `10.32.21.1` |
| Storage interface | `enP7s7.25` | `enP7s7.25` |
| Storage address | `10.32.25.31/24` | `10.32.25.32/24` |
| Fabric A interface | `enp1s0f0np0` | `enp1s0f0np0` |
| Fabric A address | `198.19.240.11/24` | `198.19.240.12/24` |
| Fabric A RDMA device | `rocep1s0f0` | `rocep1s0f0` |
| Fabric B interface | `enP2p1s0f0np0` | `enP2p1s0f0np0` |
| Fabric B address | `198.19.241.11/24` | `198.19.241.12/24` |
| Fabric B RDMA device | `roceP2p1s0f0` | `roceP2p1s0f0` |

The QSFP DAC is connected to physical port 0 on both systems. Each cage exposes
two logical network/RDMA devices, so both `/24` paths must be configured even
though there is one cable. At each endpoint, both paths share the same
ConnectX-7 and QSFP cage; end to end, they share the single DAC. They are not
independent physical rails or failure domains.

### GLKVM console

Both Sparks are connected to the stock GLKVM console at `https://10.32.20.10/`: `spark-1` is downstream port 7 and `spark-2` is port 8. The optional [Hosts bar](../../devices/glkvm/README.md#optional-hosts-bar) at `https://10.32.20.10/hosts/` adds switching buttons around the stock console. Physical switch buttons and the paced SSH helper also work:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/bin/kvm-switch 7' # spark-1
ssh root@10.32.20.10 '/etc/kvmd/user/bin/kvm-switch 8' # spark-2
```

Visually confirm the expected Spark before sending keyboard input. GLKVM provides video and USB HID but no ATX control, so a powered-off or hard-hung Spark still requires a physical power action. Appliance deployment and recovery live in [`devices/glkvm/README.md`](../../devices/glkvm/README.md).

#### Restoring a missing Spark text console

A host can be reachable over SSH and enumerate the GLKVM keyboard while producing no HDMI signal. Inspect `/proc/fb`, `/sys/class/drm/`, and the live `nvidia_drm` `modeset` parameter before resetting the KVM. On 2026-10-20, spark-1 had driver `580.173.02` with `modeset=N` from NVIDIA's `nvidia-drm-options-modeset0` package, no framebuffer or display connectors, and inactive GDM and `getty@tty1` services. A [firsthand report with this driver](https://forums.developer.nvidia.com/t/dgx-spark-ota-update-to-dgx-os-7-5-0-kills-all-display-output-when-nvidias-own-nvidia-drm-options-modeset0-package-is-installed/382452) describes the same symptoms.

Check GPU workloads and arrange an interruption window before changing display-driver state. Keep SSH connected. When `/dev/dri/card0` has no clients and `/sys/module/nvidia_drm/refcnt` is zero, try reloading only the display module and starting a text login; leave the CUDA driver and desktop manager alone:

```sh
ssh kelchm@10.32.21.31 'sudo -n sh -s' <<'REMOTE'
set -eu
test "$(cat /sys/module/nvidia_drm/refcnt)" = 0
if fuser /dev/dri/card0 >/dev/null 2>&1; then
    echo "Display has an active client; stop before reloading" >&2
    exit 1
fi
rmmod nvidia_drm
if ! modprobe nvidia_drm modeset=1 fbdev=1; then
    modprobe nvidia_drm modeset=0 fbdev=1
    exit 1
fi
systemctl start getty@tty1
cat /proc/fb
ls /sys/class/drm/
REMOTE
```

Do not force-remove a busy module or unload `nvidia`, `nvidia_uvm`, or `nvidia_modeset`. If the attempt does not restore video, inspect connector status, available modes, EDID, and the GLKVM's `/sys/bus/i2c/devices/1-002b/resolution` before further changes. The module options above are temporary; a rollback to `modeset=0 fbdev=1` uses the same guarded display-module reload and must stop if the module is busy. A host reboot or physical power cycle is a separate escalation after saving workload state.

Validated live on spark-1 on 2026-10-20: the display-only reload completed without stopping the TensorFold process, created `nvidia-drmdrmfb` and an enabled HDMI connector, and restored a visible `spark-1 login:` prompt at `1920x1080@60`. The host recorded both a Right-arrow press and release from GLKVM. TensorFold retained PID 846013 and its 109,309 MiB allocation, and `/health` still reported `status: ok`. GDM remained stopped; `getty@tty1` was started and was already enabled. No change was made to spark-2 or the existing boot target.

After successful live checks, persist the site override from [`sparks/console/zz-spark-console.conf`](../../sparks/console/zz-spark-console.conf). It sorts after the vendor override and enables both modesetting and a framebuffer without editing the package-owned file. Back up existing initramfs images outside `/boot` before rebuilding them. Applied on spark-1 on 2026-10-20: both installed initramfs images (`6.11.0-1014-nvidia` and `6.17.0-1031-nvidia`) were regenerated and contain the site override, and `modprobe -c` listed it after the vendor's `modeset=0` option. That run's historical backups are in `/var/backups/spark1-console.l3WC9o`; do not use them for a later run. No reboot was part of the October 20 check. The later [DGX OS and firmware update](#updating-dgx-os-and-firmware) records subsequent reboot-era console evidence and the current early-boot HDMI-unplug restriction; it does not revalidate these old initramfs backups against newer kernels.

```sh
scp sparks/console/zz-spark-console.conf kelchm@10.32.21.31:/tmp/zz-spark-console.conf
ssh kelchm@10.32.21.31 'sudo -n sh -s' <<'REMOTE'
set -eu
site=/etc/modprobe.d/zz-spark-console.conf
if [ -e "$site" ]; then
    cmp /tmp/zz-spark-console.conf "$site"
fi
if grep -Eq "nvidia[-_]drm[.](modeset|fbdev)=" /proc/cmdline; then
    echo "Review explicit boot-command-line display options before continuing" >&2
    exit 1
fi
backup_dir=$(mktemp -d /var/backups/spark1-console.XXXXXX)
printf "Pre-change backup directory: %s\n" "$backup_dir"
cp -p /boot/initrd.img-*nvidia "$backup_dir/"
if [ -e "$site" ]; then
    cp -p "$site" "$backup_dir/site.conf"
fi
rollback() {
    cp -p "$backup_dir"/initrd.img-*nvidia /boot/
    if [ -e "$backup_dir/site.conf" ]; then
        cp -p "$backup_dir/site.conf" "$site"
    else
        rm -f "$site"
    fi
    sync
    printf "Persistence failed; pre-change images restored from %s\n" "$backup_dir" >&2
}
trap rollback EXIT
install -o root -g root -m 0644 /tmp/zz-spark-console.conf "$site"
rm /tmp/zz-spark-console.conf
update-initramfs -u -k all
for image in /boot/initrd.img-*nvidia; do
    lsinitramfs "$image" | grep -q "etc/modprobe.d/zz-spark-console.conf"
done
test "$(modprobe -c | grep "^options nvidia_drm " | tail -n 1)" = "options nvidia_drm modeset=1 fbdev=1"
trap - EXIT
printf "Pre-change initramfs backups: %s\n" "$backup_dir"
REMOTE
```

To undo the persistent setting, remove only `/etc/modprobe.d/zz-spark-console.conf` and rebuild initramfs again; the package-owned `modeset=0` override remains available. This does not change the currently loaded module. A runtime rollback, during an interruption window and after the same zero-reference/no-client checks, must use `modprobe nvidia_drm modeset=0 fbdev=1` after `rmmod nvidia_drm`; the recovery command above loads `modeset=1` and is not the undo command. Otherwise reboot after saving workload state. The EXIT trap restores both images and any saved site override if persistence fails. If manual recovery is needed, use the exact backup directory printed by the failed run, replacing the placeholder below. Do not mix images from a historical run with the current configuration or installed kernels:

```sh
ssh kelchm@10.32.21.31 'sudo -n sh -s' <<'REMOTE'
set -eu
backup_dir=/var/backups/spark1-console.REPLACE_WITH_CURRENT_RUN
test -d "$backup_dir"
cp -p "$backup_dir"/initrd.img-*nvidia /boot/
site=/etc/modprobe.d/zz-spark-console.conf
if [ -e "$backup_dir/site.conf" ]; then
    cp -p "$backup_dir/site.conf" "$site"
else
    rm -f "$site"
fi
sync
REMOTE
```

Future persistence changes need boot-time verification at the next authorized maintenance opportunity, respecting the early-boot HDMI restriction below. Merging this runbook does not apply or retest appliance or Spark state.

### Fabric address allocation

`198.19.240.0/20` is reserved for isolated machine fabrics. It is part of the
[RFC 2544](https://datatracker.ietf.org/doc/html/rfc2544) benchmarking block,
not part of the lab's routed `10.32.0.0/16` vocabulary. The third octet is the
logical-path identifier beginning at 240; `.11` and `.12` are member indexes
within this fabric rather than instances of the VLAN system-identity rule. A
`/24` per path permits a future switched fabric to add nodes without
renumbering.

This allocation is valid only while the fabric remains closed: no DNS or
service discovery, no gateway/router firewall object, no static or dynamic
route, and no VPN route advertisement. Host nftables rules refer to interface
names rather than fabric prefixes. If general applications or clients ever
need fabric access, renumber path A to `10.254.240.0/24` and path B to
`10.254.241.0/24` instead of weakening those boundaries.

## UniFi state

This is applied controller state, not merely desired configuration.

### Workloads network

| Setting | Value |
|---|---|
| Name | `Workloads` |
| VLAN | 21 |
| Gateway | `10.32.21.1/24` |
| DHCP range | `10.32.21.200-10.32.21.239` |
| IPv6 | Disabled |
| Zone | Internal |
| Internet access | Enabled |

No Workloads firewall rules were created during the original Spark bring-up. The later PVE commissioning session added the containment slice recorded in [`network/unifi/README.md`](../../network/unifi/README.md): Workloads cannot initiate to Infra Mgmt, Storage, K8s Prod, or `admin-prod`, and K8s Prod cannot initiate to Workloads. Main administration and `services-prod` remain reachable. The broader firewall matrix, including explicit Spark inference allows and IoT/Guest negative tests, is still unapplied and unvalidated.

### `spark-trunk` port profile

| Setting | Value |
|---|---|
| Native network | VLAN 21 `Workloads` |
| Tagged networks | VLAN 25 `Storage` only |
| Autonegotiation | Enabled |
| Flow control | Enabled |
| Energy Efficient Ethernet | Disabled |

The profile is applied to enabled Core Aggregation ports 7 and 8. Both trained
at 10GbE and had zero controller-reported switch-port RX/TX errors when checked.
UniFi reported both SFP+ modules as OEM `SFP-10G-SR`; port 8 reported about
30.7 C. Confirm the label against the physical module before using that string
for replacement procurement.

## Spark host configuration

### Base-system corrections

- Set both hosts to timezone `America/New_York`.
- The factory images initially had the same `/etc/machine-id`. Preserve the
  original under `/root/dgx-bringup-backup`, clear it, run
  `systemd-machine-id-setup`, and reboot each host. Never copy or hard-code the
  resulting IDs.
- Back up the original NetworkManager profiles and `/etc/hosts` under
  `/root/dgx-bringup-backup` before editing.
- Generate a local Ed25519 SSH key on each Spark and authorize both public keys
  on both hosts for passwordless cluster launch. Private keys remain on their
  originating Spark.

### User sessions

DGX OS is Ubuntu's desktop image. Its packages enable PipeWire, PipeWire-Pulse, filter-chain and WirePlumber in every account's service manager (`user@<uid>.service`), not only in a graphical session, so booting to `multi-user.target` doesn't stop them. Every SSH login starts that manager, and logind stops it 10 seconds after the last login ends, so SparkRun's bursts of logins started and stopped the stack about twice a minute. WirePlumber also turns its Bluetooth support on and off as each login opens and closes. A login that started the manager wrote about 90 journal lines from it, Bluetooth and rtkit; a login while it was already up still wrote 26 Bluetooth lines. On 2026-10-27, two-thirds of spark-1's journal over 30 minutes was Bluetooth lines. About hourly, the manager overran Ubuntu's 5-second stop limit (`/usr/lib/systemd/system/user@.service.d/timeout.conf`) and was killed, because the document portal that the snap desktop helper starts didn't unmount in time.

Both hosts mask the audio stack for every account and keep `kelchm`'s manager running. A mask doesn't stop running instances, so stop them in each running manager too:

```sh
units="pipewire.socket pipewire.service pipewire-pulse.socket pipewire-pulse.service filter-chain.service wireplumber.service"
sudo loginctl enable-linger kelchm
sudo systemctl --global mask $units
systemctl --user stop $units                     # kelchm's manager
sudo systemctl --user -M sparkdash@ stop $units  # spark-2 only
```

A login now writes only its own lines, such as `sshd`, `systemd-logind` and its session scope. `systemctl --global is-enabled wireplumber.service` prints `masked`, `loginctl show-user kelchm -p Linger` prints `Linger=yes`, and `pgrep -a 'pipewire|wireplumber'` prints nothing. The masks cover `sparkdash` on spark-2 too; its manager stays up through sparkDash's persistent login. Bluetooth stays enabled, but nothing registers with it. To undo, `sudo systemctl --global unmask` the same units and `sudo loginctl disable-linger kelchm`; neither needs a reboot. The GNOME desktop would need the units back for sound and screen sharing.

### NetworkManager profiles

The persistent connection names are the automation contract. Recreating a host
must yield these properties:

| Profile | Interface | IPv4 | Gateway | MTU | Other requirements |
|---|---|---|---|---|---|
| `spark-lan` | `enP7s7` | host-specific `10.32.21.31/.32/24` | `10.32.21.1` | 1500 | DNS `10.32.21.1`; IPv6 disabled; EEE disabled |
| `spark-storage` | `enP7s7.25` (VLAN 25 on `enP7s7`) | host-specific `10.32.25.31/.32/24` | none | 1500 | `ipv4.never-default yes`; IPv6 disabled |
| `cx7-fabric-a` | `enp1s0f0np0` | host-specific `198.19.240.11/.12/24` | none | 9000 | `ipv4.never-default yes`; IPv6, mDNS, and LLMNR disabled |
| `cx7-fabric-b` | `enP2p1s0f0np0` | host-specific `198.19.241.11/.12/24` | none | 9000 | `ipv4.never-default yes`; IPv6, mDNS, and LLMNR disabled |

Rename the unused physical-port-1 profiles to `cx7-unused-port1-a` and
`cx7-unused-port1-b` and set `connection.autoconnect no`. Do not delete them;
they are useful if a later topology consumes the second QSFP cage.

Verify the Realtek setting independently of NetworkManager after every reboot:

```sh
sudo ethtool --show-eee enP7s7
# EEE status: disabled
```

### Fabric hostnames

Both `/etc/hosts` files contain only these fabric-local names:

```text
198.19.240.11 spark-1-fabric-a
198.19.240.12 spark-2-fabric-a
198.19.241.11 spark-1-fabric-b
198.19.241.12 spark-2-fabric-b
```

Do not publish these names in DNS.

### Forwarding guard

Docker enables global IPv4 forwarding and resets per-interface forwarding
sysctls during boot. A per-interface sysctl therefore did not survive a reboot
and was removed. A dedicated nftables hook now drops any forwarded packet that
enters or leaves a fabric interface before Docker's forwarding rules can accept
it. This blocks ordinary bridge-networked containers from using the fabric as
an application path. It does not block traffic originating on the host,
host-network containers, or a workload deliberately given fabric/RDMA access.

`/etc/dgx-fabric-isolation.nft` on both hosts:

```nft
table inet dgx_fabric_guard {
    chain forward {
        type filter hook forward priority -10; policy accept;
        iifname { "enp1s0f0np0", "enP2p1s0f0np0" } counter drop comment "DGX fabric is non-routed"
        oifname { "enp1s0f0np0", "enP2p1s0f0np0" } counter drop comment "DGX fabric is non-routed"
    }
}
```

`/etc/systemd/system/dgx-fabric-isolation.service`:

```ini
[Unit]
Description=Block forwarded traffic to and from the DGX fabric
Before=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/nft delete table inet dgx_fabric_guard
ExecStart=/usr/sbin/nft -f /etc/dgx-fabric-isolation.nft
ExecStop=-/usr/sbin/nft delete table inet dgx_fabric_guard

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/docker.service.d/dgx-fabric-guard.conf`:

```ini
[Unit]
Requires=dgx-fabric-isolation.service
After=dgx-fabric-isolation.service
```

The dependency deliberately points from Docker to the guard. Docker cannot
start unless the guard loads, while an explicit Docker restart leaves the
guard running and its nftables table continuously installed. Stopping or
restarting the guard stops Docker first, so bridge workloads cannot use the
rule-replacement window. Do not reverse this dependency: making the guard
require Docker propagates Docker restarts into the guard and deletes the table
through `ExecStop`.

The stock `nftables.service` remains disabled. Its default configuration starts
with `flush ruleset`, which would erase Docker-managed firewall state if the
service were restarted after Docker.

Validate both persistence and behavior:

```sh
systemctl is-enabled dgx-fabric-isolation.service
systemctl is-active dgx-fabric-isolation.service
systemctl show docker.service -p Requires -p After
sudo nft list table inet dgx_fabric_guard
```

The live negative test temporarily routed `10.32.21.1/32` from Spark 1 through
Spark 2 on fabric A. All three probes were dropped; the Spark 2 guard counter
increased by exactly three packets, and the temporary route was removed. This
test passed again after the 2026-08-22 readdress and sequential reboot.

A Docker bridge-container A/B test then proved why the guard is needed. From
Spark 1 to Spark 2 fabric A, the guarded attempt received 0/3 replies, the same
container received 3/3 replies while the guard was briefly stopped, and a
post-restore attempt received 0/2 replies. After inverting the systemd
dependency, sequential Docker restarts on Spark 1 and Spark 2 left the guard
invocation IDs unchanged. Polling observed the table present for all 67 and 64
samples respectively, `nft monitor` recorded no guard-table event, and a final
bridge-container attempt on each host received 0/3 replies. Temporary test
containers and images were removed.

## Firmware and software

| Component | Recorded state |
|---|---|
| DGX OS | `7.6.0`, NVIDIA's October 2026 release, on both hosts since 2026-10-25 |
| Kernel | `7.0.0-1019-nvidia` with `kho=off`; `6.17.0-1032-nvidia` installed as the fallback |
| NVIDIA driver | `580.178.04` |
| SoC firmware (UEFI) | `0x02009e0f` (2.158.15), BIOS `5.36_0ACUM028`; Display Reserved Memory 0.5 GB |
| Boot target | `multi-user.target`; the GNOME desktop doesn't start |
| Embedded controller | `0x03000d07` (3.13.7) |
| USB-C PD controller | `0x00000519` (0.5.25) |
| ConnectX-7 firmware | `28.50.1002` on both hosts |
| Firmware manager | `nvidia-spark-mlnx-firmware-manager` `6.0.1-1` |
| NCCL | NVIDIA tag `v2.30.7-1`, commit `73cf112`, built for `sm_121` |
| nccl-tests | `2.19.7` test binary |
| Additional packages | `perftest`, `libopenmpi-dev`, `iperf3`, `traceroute` |

At bring-up the installed firmware and package payload matched, so no firmware flash was performed. The October 2026 update ([#1030](https://github.com/kelchm/home-lab/issues/1030)) upgraded the software above and flashed the SoC, EC, USB-PD and ConnectX-7 firmware; [Updating DGX OS and firmware](#updating-dgx-os-and-firmware) covers how. NCCL is built at `~/nccl`; tests are built at `~/nccl-tests` on both
hosts. Build logs are retained as `~/dgx-nccl-build.log` and
`~/dgx-nccl-tests-rebuild.log`.

Build with the explicit CUDA and MPI paths—the non-interactive shell does not
put `nvcc` on `PATH`, and nccl-tests cannot find `mpi.h` without `MPI_HOME`:

```sh
export CUDA_HOME=/usr/local/cuda
export MPI_HOME=/usr/lib/aarch64-linux-gnu/openmpi
export NCCL_HOME="$HOME/nccl/build"
export PATH="$CUDA_HOME/bin:$PATH"

git clone --branch v2.30.7-1 --depth 1 \
  https://github.com/NVIDIA/nccl.git "$HOME/nccl"
make -C "$HOME/nccl" -j"$(nproc)" src.build \
  NVCC_GENCODE='-gencode=arch=compute_121,code=sm_121'

git clone --branch v2.19.7 --depth 1 \
  https://github.com/NVIDIA/nccl-tests.git \
  "$HOME/nccl-tests"
NCCL_HOME="$NCCL_HOME" MPI_HOME="$MPI_HOME" \
  make -C "$HOME/nccl-tests" -j"$(nproc)" MPI=1
```

### Updating DGX OS and firmware

The Sparks take NVIDIA's OTA releases through apt and fwupd, one host at a time, with the inference workload stopped on both because it spans them. NVIDIA's [release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) and [update guide](https://docs.nvidia.com/dgx/dgx-spark/os-and-component-update.html) are the reference; these are the parts specific to this pair:

- **Stop the workload first.** `nvidia-spark-mlnx-firmware-manager` starts flashing the ConnectX-7 as soon as apt installs it. The new CX7 firmware loads on the next reboot.
- **Packages:** `sudo apt-get update`, then `sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade`. The release's meta package pulls in the kernel, the driver and NVIDIA's grub fragments.
- **Keep a fallback kernel whose NVIDIA modules match the new driver.** A driver update removes the modules for older kernels, so the kernel that was running is no longer bootable with the GPU. For driver `580.178.04` that is `linux-image-6.17.0-1032-nvidia` with `linux-modules-nvidia-580-open-6.17.0-1032-nvidia` `6.17.0-1032.32+1`.
- **Firmware:** `fwupdmgr refresh --force`, then `fwupdmgr upgrade --assume-yes --no-reboot-check` stages the EC, SoC and USB-PD capsules together, and one reboot applies them. The reboot took about eight minutes. Some users report USB-PD failing on the first attempt; check every version in `fwupdmgr get-devices` afterwards and rerun if one is behind.
- **spark-1's port 8888 rules.** Once the [API access policy](../../sparks/inference/sparkrun/README.md#api-access) is installed, check it after the reboot as described there. Until then its hand-loaded rules are not persistent: before the reboot, `sudo iptables-save > ~/iptables-before.rules`. Afterwards, copy the `VONK-FORGE` and `VONK-FORGE-HOST` chain declarations and every rule naming them or port 8888 into a file between `*filter` and `COMMIT`, and load it with `sudo iptables-restore --noflush`. Restoring the whole save would duplicate Docker's rules.
- **After each boot, before the second host:** the new kernel and `kho=off` in `/proc/cmdline`, every firmware version, both CX7 rails `ACTIVE` at 200G with MTU 9000 and full-size pings both ways, the 10GbE link at 10000, and all monitoring containers running.

Kernel `7.0.0-1019` enables Kexec HandOver by default. Its scratch area, about 9.3 GiB, can't hold long-term pins, so NCCL's RoCE memory registration fails with `ENOMEM` under memory pressure ([forum 383023](https://forums.developer.nvidia.com/t/dgx-spark-regression-kernel-7-0-0-1019-nvidia-causes-nccl-roce-ibv-reg-mr-iova2-enomem-6-17-0-1032-works/383023), [NV-Kernels#590](https://github.com/NVIDIA/NV-Kernels/pull/590)). NVIDIA's `nvidia-spark-grub-kho` package adds `kho=off`; confirm it is on the command line and that `/sys/kernel/debug/kho` is absent. `cma=128M` is not needed: `CmaTotal` of 0 on the 7.0 kernel is expected.

UEFI's Advanced → Advanced Menu → Platform Configuration → **Display Reserved Memory** is a fixed **0.5 GB** on both hosts, down from the 2 GB factory default. Set it again after loading UEFI defaults. The change raised `MemTotal` by 1.50 GiB, to 129,168,328 kB. Auto would choose the same size for the one display UEFI detects through the KVM ([UEFI manual](https://docs.nvidia.com/dgx/dgx-spark-uefi/advanced-tab.html#spark-uefi-advanced-display-reserved-memory)). Before booting with a display above 1440p attached, raise it to a fixed size that covers that display; NVIDIA warns that an undersized reservation causes display-driver errors. `sudo systemctl reboot --firmware-setup` boots straight into the setup UI.

Both hosts boot to `multi-user.target`. The GNOME desktop's processes held about 830 MB, and nothing here uses them; DGX Dashboard runs as its own services.

**Don't switch the KVM away from a Spark in its first few minutes after boot.** An HDMI unplug then can hang the GPU's GSP: `Xid 119` within about seven seconds, then `Xid 154` (GPU reset required). With the desktop running, `bad register read` errors on display-engine registers 0x611ef0–0x613878 follow. On 2026-10-25, unplugs about 55 seconds after boot hung spark-2 in 6 of 14 tries. That happened at both 0.5 GB and 2 GB, with or without the desktop, and with nvidia-drm kernel modesetting on (`modeset=1 fbdev=1`) as well as NVIDIA's default off. 24 controlled unplugs long after boot, idle and under full GPU load, never did. That register signature appears in [NVIDIA forum reports](https://forums.developer.nvidia.com/t/bug-when-connecting-hdmi-to-av-receiver/356179) of HDMI-triggered failures since December 2025, with no fix announced. This reproduction is reported in [forum topic 386810](https://forums.developer.nvidia.com/t/gb10-gsp-hang-xid-119-xid-154-when-hdmi-is-unplugged-about-a-minute-after-boot/386810); check it for a fix before relaxing the rule. A wedged host can't shut down cleanly: `nvidia-persistenced` blocks the reboot until the hardware watchdog resets the host. systemd first spends about six minutes on the service's stop timeouts. Only then does it arm the watchdog. `/etc/systemd/system.conf.d/90-reboot-watchdog.conf` sets `RebootWatchdogSec=2min` on both hosts, down from systemd's 10-minute default, so a reboot completes about eight minutes after it's requested. To recover sooner from a host already in that state, stop its containers if it still responds, then force the reboot with `sudo sh -c 'echo s > /proc/sysrq-trigger; echo b > /proc/sysrq-trigger'`. It syncs filesystems but skips the normal service shutdown.

## Validation record

Initial bring-up results are from 2026-08-21. Fabric readdress, RDMA/NCCL,
forwarding-guard, and reboot-persistence results are from 2026-08-22.

| Gate | Result | Status |
|---|---|---|
| Static routes after reboot | Default only via `10.32.21.1`; VLAN 25 and both fabric `/24`s connected-only | Pass |
| Storage L2 reachability | Both storage source IPs reached NAS `10.32.25.5` in about 0.3-1.0 ms | Partial; throughput deferred |
| Fabric jumbo frames | 8972-byte ICMP payload on both logical paths, 0% loss | Pass |
| Raw RDMA, concurrent paths | 98.04 Gb/s per path; 196.08 Gb/s aggregate before and after readdress | Pass |
| NCCL all-gather, 16 GiB, warmed | Initial 23.82 GB/s; post-fabric-readdress 24.05 GB/s; post-LAN-readdress 22.47 GB/s; zero wrong values | Pass |
| Routed Spark to Main client | 1.09 Gb/s to Wi-Fi MBP `10.32.10.244` | Healthy end-to-end baseline |
| Fabric transit | Routed negative test dropped 3/3 packets; bridge-container A/B passed only with the guard absent | Pass; guard closes a proven Docker forwarding path |
| Guard across Docker restart | Unchanged guard invocation IDs; table present in 67/67 and 64/64 samples; no guard-table nft events | Pass |
| Reboot persistence | All four profiles, EEE-off, jumbo reachability, and nftables guard returned | Pass |
| Spark to NAS iperf3 + one-hop trace | Not run; DSM work deferred | Open |
| RTL8127 10-minute full-duplex burn | Standard `rx_errors`, `rx_missed`, and `rx_mac_error` remained zero on both hosts | Pass; vendor counter monitored |
| RTL8127 captured line-rate sample | 9.40 Gb/s simultaneously in each direction for 60 seconds; zero TCP retransmits | Pass |
| IoT to Spark negative test | Firewall intentionally deferred | Blocked by firewall phase |
| Gateway has no fabric route | No route was added for `198.19.240.0/20`; read-back verification pending | Open |

The Main endpoint was a MacBook Pro on Wi-Fi. The result is a useful routed
end-to-end client baseline and is strong for that path, but it does not isolate
or establish the gateway's maximum forwarding capacity.

The 10-minute burn started with `rx_errors=0` on both hosts and ended the same
way. The driver-specific `rx_mac_missed` counter increased from 356 to 642 on
Spark 1 and from 336 to 622 on Spark 2. A captured 60-second repeat then ran at
9.40 Gb/s in both directions with zero TCP retransmits and zero standard kernel
RX errors, drops, or missed packets; `rx_mac_missed` increased by another 26 on
each host. This does not fail the defined `rx_errors` gate, but retain it as a
trend to compare after driver updates or any reported link instability.

### Raw RDMA command shape

Run one server per logical path on Spark 2, then both clients concurrently on Spark 1:

```sh
# Spark 2
ib_write_bw -d rocep1s0f0 -x 3 -F -D 10 -s 1048576 -q 4 \
  --report_gbits -p 18515
ib_write_bw -d roceP2p1s0f0 -x 3 -F -D 10 -s 1048576 -q 4 \
  --report_gbits -p 18516

# Spark 1
ib_write_bw -d rocep1s0f0 -x 3 -F -D 10 -s 1048576 -q 4 \
  --report_gbits -p 18515 198.19.240.12
ib_write_bw -d roceP2p1s0f0 -x 3 -F -D 10 -s 1048576 -q 4 \
  --report_gbits -p 18516 198.19.241.12
```

### NCCL environment and command

Management/bootstrap traffic is pinned to 10GbE. NCCL data is restricted to
the two exact RoCE devices and IPv4 RoCEv2 GID index 3. Leave NIC merging and
cross-NIC policy at NCCL defaults; NCCL schedules channels across the two
separate logical devices without fusing them.

```sh
export CUDA_HOME=/usr/local/cuda
export MPI_HOME=/usr/lib/aarch64-linux-gnu/openmpi
export NCCL_HOME="$HOME/nccl/build"
export LD_LIBRARY_PATH="$NCCL_HOME/lib:$CUDA_HOME/lib64:$MPI_HOME/lib:${LD_LIBRARY_PATH:-}"
export UCX_NET_DEVICES=enP7s7
export NCCL_SOCKET_IFNAME='=enP7s7'
export OMPI_MCA_btl_tcp_if_include=enP7s7
export NCCL_IB_HCA='=rocep1s0f0,roceP2p1s0f0'
export NCCL_IB_GID_INDEX=3

mpirun -np 2 -H 10.32.21.31:1,10.32.21.32:1 \
  -x LD_LIBRARY_PATH -x UCX_NET_DEVICES -x NCCL_SOCKET_IFNAME \
  -x OMPI_MCA_btl_tcp_if_include -x NCCL_IB_HCA \
  -x NCCL_IB_GID_INDEX \
  "$HOME/nccl-tests/build/all_gather_perf" \
  -b 16G -e 16G -f 2 -w 5 -n 30
```

Confirm `show_gids` maps index 3 to RoCEv2/IPv4 on both active RDMA devices
before reusing the command after a driver or firmware change.

## Troubleshooting record

### Link is up but RDMA is near 13 Gb/s

The first run produced 13.40 Gb/s on a single path even though both logical
interfaces reported up. The QSFP DAC was not fully seated. After reseating both
ends and rebooting with the final cable topology already connected, one path
reached 111.87 Gb/s and both simultaneous paths reached 196.08 Gb/s.

Treat a low-but-nonzero result as a physical/link-initialization problem before
changing firmware or NCCL settings:

1. Reseat the slippery QSFP retention mechanism at both ends.
2. Verify the same physical port number is used on both Sparks.
3. Reboot both Sparks with the final cable connected.
4. Confirm both logical port-0 interfaces are up with `ibdev2netdev`.
5. Verify jumbo pings, then rerun `ib_write_bw` before NCCL.

### nccl-tests cannot find `mpi.h`

`libopenmpi-dev` may be installed while the test build still fails with
`fatal error: mpi.h: No such file or directory`. Export
`MPI_HOME=/usr/lib/aarch64-linux-gnu/openmpi`, clean the partial test build, and
rerun `make MPI=1`.

### Per-interface forwarding returns after reboot

Docker changes forwarding sysctls during startup. Do not rely on
`net.ipv4.conf.<fabric>.forwarding=0` as the only control. Verify the
`dgx-fabric-isolation` service and nftables counters instead.

### `rx_mac_missed` grows under full-duplex line rate

The Realtek `r8127` 11.014.00-NAPI driver exposes a vendor-specific
`rx_mac_missed` statistic that grew symmetrically during saturation. The
standard kernel RX error, drop, and missed counters stayed at zero, iperf3
reported zero retransmits, and both directions sustained 9.40 Gb/s. Record and
trend the vendor counter, but do not equate it with `rx_errors`. Reopen the NIC
investigation if it correlates with standard-counter growth, retransmits, link
flaps, or application-visible loss.

### Management SSH drops with NIC carrier loss

Match `journalctl -k` carrier timestamps on `enP7s7` against UniFi's **Receive SFP Signal Loss** events and the switch port's cumulative `link_down_count`. On September 30, spark-2 recorded carrier loss beginning at 20:42 EDT; Core Aggregation port 8 independently reported `SFP_RECEIVED_FAULT` at 21:42:15 and 21:48:26. Drops continued after inference stopped. Both ports retained `spark-trunk`, host and profile EEE were disabled, and UniFi showed no configuration changes in the last day. These observations locate the failing link but do not distinguish the cable, SFP-to-Ethernet module, switch port or host PHY. Zero switch-port RX/TX errors and a reported module temperature near 32°C do not rule out those faults.

The retained host journal also contains carrier drops on September 28 and earlier dates, so carrier loss predates this recipe trial. Earlier events have not been classified as operational resets or unplanned faults; they do not establish that every historical drop has the same cause. The since-boot journal and current host/switch receipts are retained outside git under `~/sparkrun/experiments/glm53-20260930/receipts/` on spark-1.

The live switch port name is `SFP+ 8`. Use the inventory map rather than relying on auto-generated client aliases. Inspect the controller at `https://unifi.home.kelch.io/`. Its generic OEM `SFP-10G-SR` identity must be checked against the physical module before replacement procurement. Keep identifying details and raw diagnostics local; public issues need only a short status note.

When worker management SSH is unavailable but spark-1 is reachable, use the existing private fabric only for explicit operator diagnostics. Preserve host-key verification by matching the worker's already verified management identity:

```sh
ssh kelchm@10.32.21.31 \
  "ssh -o BatchMode=yes -o HostKeyAlias=10.32.21.32 -o ConnectTimeout=5 kelchm@198.19.240.12 'ip -s link show enP7s7; sudo -n journalctl -k --since \"1 hour ago\" --no-pager'"
```

Save host/switch evidence before resetting a link or rebooting. Stop affected inference workloads through their corresponding SparkRun recipe. Test a known-good short cable first, then a known-good module, changing one component at a time. If neither resolves the fault, test a spare switch port with the same native Workloads VLAN 21 and tagged Storage VLAN 25 profile before pursuing the host NIC/driver. Record counter deltas; cumulative counts are not a count of the current incident. Reaccept the link only after management/storage probes succeed, at least 30 minutes idle and a complete C4 250k cold/replay pair produce no new carrier or SFP receive-signal-loss events, and the cause/fix is recorded in #649.

## Remaining work

Do not mark this runbook complete until these are resolved:

- [ ] Resolve spark-2 management/storage carrier loss and pass the recovery gate in [#649](https://github.com/kelchm/home-lab/issues/649).
- [ ] In DSM, identify only the NFS exports the Sparks need and scope them to
      `10.32.25.31` and `10.32.25.32`. Do not broaden unrelated exports.
- [ ] Run Spark-to-NAS `iperf3` over VLAN 25 and record at least 9 Gb/s plus a
      direct/one-hop trace. No DSM or export changes were made on 2026-08-21.
- [x] Add `spark-1.home.kelch.io`, `spark-2.home.kelch.io`, and the two storage DNS records. The four UniFi-local records were added and resolved through the VLAN 20 gateway on 2026-08-26. Fabric names remain `/etc/hosts` only.
- [ ] Read back the gateway routing table and prove that no route for
      `198.19.240.0/20` or either fabric prefix is present.
- [ ] Apply the network-plan firewall matrix in its own phase, then run the IoT
      SSH negative test and Main inference-port positive test.
- [ ] Remove `/etc/sudoers.d/99-dgx-bringup` from both Sparks and verify
      `sudo -n true` fails before declaring the commissioning session closed.
- [x] Remove obsolete `known_hosts` entries: the bring-up DHCP addresses
      (`10.32.21.228/.231`, matched by host key) and the superseded statics
      `10.32.21.11/.12` are removed on the operator machine and both Sparks.
      The corresponding stale controller leases age out on their own.

## Rollback

Rollback is host-by-host so KVM remains available throughout:

1. Disable the affected aggregation switch port.
2. Use KVM to restore NetworkManager profiles and `/etc/hosts` from
   `/root/dgx-bringup-backup`.
3. Stop Docker, remove
   `/etc/systemd/system/docker.service.d/dgx-fabric-guard.conf`, and reload
   systemd before disabling `dgx-fabric-isolation.service`; remove its nftables
   table only after the fabric profiles are down.
4. Remove the cluster public keys from `authorized_keys` if the pair is being
   separated. Never copy a private key between hosts.
5. Reboot, verify console access, then re-enable only the required LAN port.

The UniFi VLAN and `spark-trunk` profile can remain inert with both member ports
disabled. Delete them only after confirming no future workload host consumes
VLAN 21.

## Automation boundary

A future automation pass should consume a small per-host inventory and own the
repeatable host state, while keeping physical cabling, UniFi adoption, secrets,
and destructive firmware actions gated manually.

Good first automation targets:

- Packages and pinned NCCL source/tag builds.
- NetworkManager profiles from per-host address variables.
- `/etc/hosts`, the nftables guard, and its systemd unit.
- EEE verification and route/MTU/GID assertions.
- Idempotent `iperf3`, `ib_write_bw`, and NCCL acceptance scripts that emit a
  dated result artifact.

Keep these manual or approval-gated:

- Switch-port enablement and VLAN/firewall changes.
- QSFP/SFP module and cable placement.
- Machine-ID regeneration and firmware flashing.
- SSH private-key generation/distribution and DSM export changes.

The tables in this runbook are the initial inventory schema; automation should
derive configuration from them rather than embedding node-specific conditionals.
