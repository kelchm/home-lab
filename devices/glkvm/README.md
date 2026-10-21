# GL.iNet KVM customizations

Configuration for the GL-RM1PE at `10.32.20.10`. This is manually applied
appliance state; Flux does not reconcile it.

Firmware `V1.10.1 release2` (`/etc/version`), build `rm10-1.10.0beta2-30-g265a7f2656-dirty` (`/etc/os-release`), kvmd 4.82, verified 2026-10-17. GL.iNet builds RM1PE firmware from its RM10 tree, so the build string names the RM10 while `/etc/version` and `/proc/gl-hw-info/model` say RM1PE.

## Web interfaces

The stock GL.iNet UI serves the console, firmware updates, and appliance management at `https://10.32.20.10/`. The PiKVM frontend is disabled. An optional [Hosts bar](#optional-hosts-bar) at `/hosts/` frames the stock console and adds downstream switching buttons; the normal root address remains the stock UI.

| Port | Interface |
|------|-----------|
| 443 | Stock GLKVM |
| 8888 | Disabled |

Port 80 redirects to HTTPS. The active nginx configuration is copied unchanged from `/rom/etc/kvmd/nginx-kvmd.conf`, supplied by the installed firmware, and uses the certificate in `/etc/kvmd/user/ssl`. Do not restore an old custom nginx file after a firmware update.

Restored 2026-10-20 by validating and copying the firmware's nginx configuration, then reloading nginx. The prior web configuration is backed up on the appliance at `/etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui-20261020`. The USB compatibility fix, monitoring, and SSH helpers remain applied; switching web interfaces does not require rebuilding USB or restarting kvmd.

## Downstream KVM controls

The physical eight-port KVM switches inputs with `Left Ctrl`, `Left Ctrl`,
then the port number. GL.iNet's shortcut UI sends those events too quickly for
the switch. [`kvm-switch.py`](kvm-switch.py) writes standard eight-byte USB HID
keyboard reports with a 75 ms key hold and a 250 ms inter-key delay.

The downstream mapping is recorded below and in [`override.yaml`](override.yaml). The override retains the command channels previously used by the PiKVM Hosts menu, but the stock GL.iNet UI does not display them. Use the optional Hosts bar, physical switch buttons, or the paced SSH helper to select a host:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/bin/kvm-switch 2'
```

| KVM port | Host |
|----------|------|
| 1 | `pve-sbx-1` |
| 2 | `pve-sbx-2` |
| 3 | `pve-sbx-3` |
| 4 | `k8s-prod-1` |
| 5 | `k8s-prod-2` |
| 6 | `k8s-prod-3` |
| 7 | `spark-1` |
| 8 | `spark-2` |

All eight downstream ports are assigned. Update this table and the matching command-channel label in the override together if the physical mapping changes. Always confirm the selected host on screen before sending further input.

### Optional Hosts bar

[`hosts-prototype/`](hosts-prototype/) adds eight host buttons at `https://10.32.20.10/hosts/` above an iframe containing the unmodified stock console. Sign in inside that console to enable the buttons. The page reuses the existing same-origin session cookie and protected GPIO API; it does not store credentials or add a backend service. The **Stock UI** link opens the normal root address directly.

Only one switch request can run at a time. The page waits for the existing paced helper to finish and labels the result **Requested**, because the physical switch provides no active-port readback. Confirm the hostname on screen before typing. An interrupted, failed, or ten-second timeout response disables switching until reload. Check the console and any blocked helper before reloading: canceling a browser request does not stop a helper blocked on USB, and repeated reloads can still queue commands. See [USB class compatibility](#usb-class-compatibility) for helper diagnosis.

Install or update from the repository root after applying the normal appliance configuration:

```sh
devices/glkvm/hosts-prototype/install.sh
```

The installer requires the main nginx configuration to match the firmware copy, backs up any previous prototype, installs static assets under `/etc/kvmd/user/hosts-prototype/`, and adds `/usr/share/kvmd/extras/hosts-prototype/nginx.ctx-server.conf` through the firmware's existing include. It validates nginx before reloading it; no kvmd restart or USB rebuild is needed. The added route makes only these static files public, like the stock login page; `/api/gpio` and pulse requests still require authentication. Firmware updates may remove the extra include file, so reapply the installer after checking the updated stock configuration.

Installed 2026-10-20. The operator confirmed that the embedded stock console and switching buttons work together. Live checks verified the three served assets, unchanged firmware main configuration, and unauthenticated GPIO reads and pulse requests returning 401. Local browser checks covered all eight channel mappings, concurrent-click suppression, expired-session recovery, malformed responses, and timeout lockout; desktop and phone layouts were inspected. This remains an optional prototype, with no port readback or cross-tab request lock.

To disable the prototype, remove only its nginx include, validate, and reload; the stock console remains available:

```sh
ssh root@10.32.20.10 '
    set -eu
    rm -f /usr/share/kvmd/extras/hosts-prototype/nginx.ctx-server.conf
    nginx -t -p /etc/kvmd/nginx -c /etc/kvmd/nginx-kvmd.conf \
        -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"
    kill -HUP "$(cat /run/kvmd/nginx.pid)"
'
```

### USB class compatibility

On `V1.10.1 release2`, leaving `otg.devices.camera.enabled` true makes the entire USB gadget advertise class/subclass/protocol `EF/02/01` (an IAD composite device), even when no USB camera function is linked. The downstream switch's Hotkey port configures this device but does not consume keyboard or mouse reports. Physical channel buttons and HDMI video still work. [`override.yaml`](override.yaml) disables this unused USB camera capability so a freshly created gadget uses `00/00/00` and its interfaces identify themselves as HID. HDMI capture is separate and unaffected by this setting. Keyboard and both mice remain enabled; virtual media remains disabled through `user/boot.yaml`.

Verified 2026-10-20: the original class accepted zero empty keyboard reports, changing only those three class bytes allowed 12 successive reports, restoring the original class reproduced the failure, and returning to `00/00/00` restored it again. The existing switch helper then selected port 2 (`pve-sbx-2`), its host enumerated all three HID interfaces, and paced typing appeared at its login prompt and was erased without submission. Rebuilding the gadget from the saved override also produced `00/00/00`; switching between ports 1 and 2 worked, and pve-sbx-2 recorded a Right-arrow press and release in its input events after the rebuild. The Logitech USB identity was unchanged. An appliance reboot was not part of this validation; recheck the class bytes, linked functions, and actual input after the next appliance reboot. The exact time the incompatible setting first appeared is unknown.

Changing the override requires rebuilding the USB gadget; restarting only kvmd does not change its USB descriptors. Run **Apply** below to install the override and rebuild the gadget. Stop kvmd and wait for its HID workers to exit before tearing down the gadget: rebuilding while they held open HID devices produced a kernel reference-count warning on this firmware. This briefly disconnects keyboard and mouse from the selected host and drops open console sessions. No host reboot is needed. After applying, check the live class bytes and linked functions:

```sh
ssh root@10.32.20.10 '
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceClass
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceSubClass
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceProtocol
    ls -l /sys/kernel/config/usb_gadget/rockchip/configs/b.1/
'
```

Expect `0x00` three times and only `hid.usb0`, `hid.usb1`, and `hid.usb2` linked. Then verify actual channel selection and one harmless key at a safe screen. USB state `configured`, an existing `/dev/hidg0`, and healthy Prometheus metrics do not prove that input works.

If switch clicks hang, inspect `ps -ef` and each helper's `/proc/<pid>/wchan`. A helper blocked in `f_hidg_write` can hold `/run/kvmd-kvm-switch.lock` indefinitely while later clicks queue behind it. Stop those specific `kvm-switch` processes before reconnecting USB so old channel selections cannot fire after recovery. The helpers have no write or lock timeout ([#928](https://github.com/kelchm/home-lab/issues/928)). Power-cycling the switch, rebooting the GLKVM, changing USB identity, and unlinking the mice did not restore input while the class was `EF/02/01`.

## Remote console helpers

The repository also preserves the standalone tools used for unattended BIOS,
boot-menu, and console work:

- [`hid-type.py`](hid-type.py) sends named keys, paced sequences, literal text,
  and Ctrl-Alt combinations through `/dev/hidg0`.
- [`hid-hold-key.py`](hid-hold-key.py) repeatedly reasserts one key through USB
  re-enumeration during POST. The proven HP Startup Menu sequence is Esc
  (`0x29`) for 30 seconds; F9/F10/F12 are `0x42`/`0x43`/`0x45`.
- [`capture-screen.sh`](capture-screen.sh) copies a JPEG directly from kvmd's
  `/run/kvmd/ustreamer.sock` to the operator workstation.

For the end-to-end HP Startup Menu, PXE, and PVE installation sequence, use the [PVE node bootstrap runbook](../../docs/runbooks/pve-node-bootstrap.md). For DGX host networking and recovery, use the [DGX Spark bring-up runbook](../../docs/runbooks/dgx-spark-bringup.md). This README remains the appliance configuration and recovery source.

The GLKVM starts `ustreamer` only while an authenticated KVM viewer is active
and stops it about ten seconds after the final viewer disconnects. The Unix
socket pathname can therefore exist while refusing connections. Open the KVM
video page first, keep it open while taking unattended captures, and treat the
helper's demand-stream error as a viewer-state problem rather than a lost HDMI
or HID path. The helper removes partial JPEGs when capture fails.

The Python helpers are installed in `/etc/kvmd/user/bin`, which normally
survives a firmware update as the appliance's persistent user partition. It is
not assumed to survive a factory reset or every future update; Git remains
authoritative. Convenience copies in `/tmp` are not authoritative and disappear
on reboot. All HID tools share `/run/kvmd-kvm-switch.lock`; do not run two input
sequences concurrently.

## Monitoring

vmagent in `k8s-prod` scrapes kvmd's native export at `https://10.32.20.10/api/export/prometheus/metrics` every 30 seconds as `job="glkvm-kvmd"`, `instance="glkvm"`, `platform="glkvm"`. The scrape and alert rules are in [`glkvm.yaml`](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/glkvm.yaml) and the **GLKVM / Recovery console** dashboard is in Grafana's `Platforms/GLKVM` folder. Alerts route like every other alert; see the [alerting runbook](../../docs/runbooks/alerting.md).

Applied 2026-10-17 on `V1.10.1 release2`. Before the patch, the unauthenticated metrics path returned HTTP 500; after it, the path returns the metrics below while `/api/info` still returns 401, vmagent reports the target up, and `GLKVMMetricsUnavailable` cleared. After restoring the stock UI on 443 on 2026-10-20, those HTTP checks still pass and no mass-storage function is linked. `/etc/init.d/S98kvmd restart` prints `Stopping kvmd: FAIL` because it checks for the old process before it has exited; one kvmd daemon was running afterwards.

The appliance needs two changes. **Apply** installs the override with the rest of the console configuration; the export patch is installed separately, below, so a firmware change that invalidates it never blocks restoring the console.

- [`override.yaml`](override.yaml) sets upstream kvmd's `prometheus.auth.enabled: false`, which exempts only the metrics path from login. kvmd has no read-only role: any account that could read the metrics could also type into every host's BIOS, toggle virtual media and upgrade firmware. Leaving the path unauthenticated keeps every console credential out of the cluster, at the cost of letting anyone who can reach port 443 read the appliance's temperature, CPU, memory and network counters, and the ATX and GPIO rows the scrape drops. Every other path, including `/api/info`, still requires login.
- [`export.py`](export.py) fixes the export. The vendor module asks for a `fan` source that this firmware does not register, so every request fails with HTTP 500. The replacement shadows the vendor's sourceless `export.pyc` and reproduces its bytecode except for that request. It carries the SHA-256 of the `export.pyc` it was verified against and runs the vendor module unchanged when that file differs, so a firmware update cannot leave stale code in place. The install below refuses a different `export.pyc`.

What the export provides on this firmware:

| Metric | Meaning |
|---|---|
| `pikvm_hw_temp_cpu` | SoC temperature in °C. The SoC's only thermal trip is critical, at 115 °C, where the kernel shuts the appliance down. |
| `pikvm_hw_cpu_percent` | CPU in use since kvmd's previous health reading. |
| `pikvm_hw_mem_percent`, `_total`, `_available` | Memory. The appliance has about 1 GiB, plus a compressed swap device in RAM (`/dev/zram0`, about 490 MiB) that this firmware adds. |
| `pikvm_hw_net_bytes_sent`, `_recv` | Bytes on every interface except loopback since boot; use `rate()`. |

The scrape drops `pikvm_atx_*` and `pikvm_gpio_*`. ATX is not wired, so its power state means nothing. The GPIO rows are the retained host-switch command channels, which only pulse a switch sequence; they do not read back the selected port, and the vendor code reports each channel's state under both its `online` and `state` names. The export has no HDMI signal, USB enumeration, selected port, uptime or filesystem metrics, and nothing in it observes the host on the selected port. A blank screen or missing video is not an appliance failure. Load average stays between 8 and 10 because of the SoC's media threads and is not a useful signal.

The appliance serves its vendor's self-signed certificate (`CN=localhost`, expired 1979), so the scrape skips verification and there is no certificate-expiry alert; [#419](https://github.com/kelchm/home-lab/issues/419) tracks a real certificate.

| Alert | Fires when |
|---|---|
| `GLKVMMetricsUnavailable` | The scrape fails or is missing for 10 minutes. |
| `GLKVMHot` | The SoC is above 95 °C for 15 minutes. |
| `GLKVMMemoryLow` | Memory is over 90% used for 30 minutes. |
| `GLKVMHealthMetricsMissing` | The export answers without temperature or memory for 30 minutes, so the rule that reads the missing metric cannot fire. |

When `GLKVMMetricsUnavailable` fires, the status of an unauthenticated request separates the causes:

```sh
curl -sk -o /dev/null -w '%{http_code}\n' https://10.32.20.10/api/export/prometheus/metrics
```

| Status | Cause |
|---|---|
| 200 | The endpoint answers this client; check vmagent's target status and its network policy. It says nothing about video or keyboard. |
| 401 | The `prometheus` override is missing, usually after a firmware update. |
| 500 | The export patch is missing or stood aside for a different `export.pyc`. |
| 502 | kvmd is down; nginx still answers. |
| No connection | The appliance or its network. |

Install the export patch after **Apply**. It stops before changing anything unless the appliance's `export.pyc` is the one the patch was verified against and `export.py` is absent or a previous copy of the patch:

```sh
scp -O devices/glkvm/export.py root@10.32.20.10:/tmp/export.py
ssh root@10.32.20.10 '
    set -eu
    api=/usr/lib/python3.12/site-packages/kvmd/apps/kvmd/api
    expected=$(sed -n "s/^_VENDOR_PYC_SHA256 = \"\([0-9a-f]*\)\"$/\1/p" /tmp/export.py)
    actual=$(sha256sum "$api/export.pyc" | cut -d" " -f1)
    test -n "$expected"
    test "$actual" = "$expected"
    test ! -e "$api/export.py" || grep -q "^_VENDOR_PYC_SHA256 = " "$api/export.py"
    cp /tmp/export.py "$api/export.py"
    rm -f /tmp/export.py
    /etc/init.d/S98kvmd restart
'
```

Restarting kvmd drops open console sessions for a few seconds; do it while no host needs the console. The file lives in the root overlay, like `/etc/kvmd/override.yaml`, so it survives a reboot. Whether a firmware update keeps it is not verified; the hash check makes either outcome safe. Then confirm the export and that everything else still requires login:

```sh
ssh root@10.32.20.10 '
    set -eu
    /etc/init.d/S98kvmd status
    curl -ksS https://127.0.0.1/api/export/prometheus/metrics | grep -q "^pikvm_hw_temp_cpu "
    test "$(curl -ksS -o /dev/null -w "%{http_code}" https://127.0.0.1/api/info)" = 401
'
```

In VictoriaMetrics, `up{job="glkvm-kvmd"}` is 1 within a minute of the vmagent policy and scrape being live.

To remove monitoring from the appliance, delete the `prometheus` block from `/etc/kvmd/override.yaml`, then `rm /usr/lib/python3.12/site-packages/kvmd/apps/kvmd/api/export.py` and `/etc/init.d/S98kvmd restart`. The metrics path then requires login again and returns HTTP 500 once logged in, as shipped.

## Apply

The appliance's Dropbear server does not provide an SFTP subsystem, so force
legacy SCP mode with `-O`:

```sh
scp -O devices/glkvm/kvm-switch.py root@10.32.20.10:/tmp/kvm-switch
scp -O devices/glkvm/hid-type.py root@10.32.20.10:/tmp/hid-type
scp -O devices/glkvm/hid-hold-key.py root@10.32.20.10:/tmp/hid-hold-key
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml

ssh root@10.32.20.10 '
    set -eu
    mkdir -p /etc/kvmd/user/bin /etc/kvmd/user/backups
    if [ -e /etc/kvmd/override.yaml ]; then
        cp /etc/kvmd/override.yaml \
            /etc/kvmd/user/backups/override.yaml.before-kvm-controls
    fi
    cp /tmp/kvm-switch /etc/kvmd/user/bin/kvm-switch
    cp /tmp/hid-type /etc/kvmd/user/bin/hid-type
    cp /tmp/hid-hold-key /etc/kvmd/user/bin/hid-hold-key
    chmod 0755 /etc/kvmd/user/bin/kvm-switch \
        /etc/kvmd/user/bin/hid-type /etc/kvmd/user/bin/hid-hold-key
    cp /tmp/override.yaml /etc/kvmd/override.yaml
    if [ -e /etc/kvmd/nginx-kvmd.conf ] && \
        [ ! -e /etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui ]; then
        cp /etc/kvmd/nginx-kvmd.conf \
            /etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui
    fi
    nginx -t -p /etc/kvmd/nginx -c /rom/etc/kvmd/nginx-kvmd.conf \
        -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"
    cp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    rm -f /tmp/kvm-switch /tmp/hid-type /tmp/hid-hold-key \
        /tmp/override.yaml
    command -v timeout >/dev/null
    command -v pkill >/dev/null
    restore_kvmd() {
        if ! pgrep -f "[k]vmd/main" >/dev/null; then
            /etc/init.d/S98kvmd start
        fi
    }
    trap restore_kvmd EXIT
    # Discard queued input before USB returns. SIGKILL skips blocked cleanup writes.
    helpers="^[^ ]*python3 /etc/kvmd/user/bin/(kvm-switch|hid-type|hid-hold-key)( |$)"
    /etc/init.d/S98kvmd stop
    workers="[k]vmd/(main|hid-|streamer)|$helpers"
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        pkill -KILL -f "$helpers" || true
        pgrep -f "$workers" >/dev/null || break
        sleep 1
    done
    if pgrep -f "$workers" >/dev/null; then
        echo "kvmd workers have not exited; USB gadget was not rebuilt" >&2
        exit 1
    fi
    timeout 15 /usr/bin/kvmd-otg stop
    /usr/bin/kvmd-otg start
    /etc/init.d/S98kvmd start
    trap - EXIT
    if [ -f /run/kvmd/pikvm-nginx.pid ]; then
        kill "$(cat /run/kvmd/pikvm-nginx.pid)" 2>/dev/null || true
    fi
    /etc/init.d/S99kvmd-nginx restart
'
```

Confirm `kvmd` restarted, the stock UI responds, and only the expected HID functions are linked. Keep an authenticated stock-console viewer open for the screenshot check:

```sh
ssh root@10.32.20.10 '
    set -eu
    /etc/init.d/S98kvmd status
    cmp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    curl -ksS https://127.0.0.1/ | grep -q "<title>GLKVM</title>"
    if curl -ksS --connect-timeout 2 https://127.0.0.1:8888/ >/dev/null 2>&1; then
        echo "Unexpected web listener on port 8888" >&2
        exit 1
    fi
    for field in bDeviceClass bDeviceSubClass bDeviceProtocol; do
        test "$(cat /sys/kernel/config/usb_gadget/rockchip/$field)" = 0x00
    done
    python3 -c "from pathlib import Path; assert sorted(p.name for p in Path(\"/sys/kernel/config/usb_gadget/rockchip/configs/b.1\").iterdir() if p.is_symlink()) == [\"hid.usb0\", \"hid.usb1\", \"hid.usb2\"]"
    test -c /dev/hidg0
    test -S /run/kvmd/ustreamer.sock
    python3 -m py_compile /etc/kvmd/user/bin/kvm-switch \
        /etc/kvmd/user/bin/hid-type /etc/kvmd/user/bin/hid-hold-key
'
devices/glkvm/capture-screen.sh /tmp/kvm-snap.jpg
```

Only send a live HID smoke-test key when the attached host is at a screen where
that input is harmless. For example:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/bin/hid-type down'
```

## Firmware-update checklist

Before an appliance update, record the running firmware and back up the applied
state from `/etc/kvmd/override.yaml`, `/etc/kvmd/nginx-kvmd.conf`, and
`/etc/kvmd/user/` outside the appliance. One simple workstation-side backup is:

```sh
backup_dir=$(mktemp -d)
scp -O -r root@10.32.20.10:/etc/kvmd/user "$backup_dir/"
scp -O root@10.32.20.10:/etc/kvmd/override.yaml "$backup_dir/"
scp -O root@10.32.20.10:/etc/kvmd/nginx-kvmd.conf "$backup_dir/"
```

After the update:

1. Confirm SSH, `/dev/hidg0`, and `/run/kvmd/ustreamer.sock` exist, and that `ls /sys/kernel/config/usb_gadget/rockchip/configs/*/` links no `mass_storage` function.
2. Keep the firmware's stock nginx configuration; do not restore the old PiKVM web configuration from a backup.
3. Re-run **Apply** to restore the known files and modes. The USB gadget rebuild applies the camera-disable override; confirm the three device class bytes are `00/00/00` as described under **USB class compatibility**.
4. Run the non-interactive checks above and capture a screen.
5. With a host at a safe menu, verify one paced HID key and one downstream KVM
   channel switch.
6. Recheck the [monitoring](#monitoring) patch against the new firmware's `export.pyc` before reinstalling it. The installed copy steps aside on its own when that file changes, and its install refuses a module it was not verified against.

The update to `V1.10.1 release2` kept `/etc/kvmd/user`, the mass-storage settings in `user/boot.yaml`, and `/etc/kvmd/override.yaml`, but replaced `/etc/kvmd/nginx-kvmd.conf` with the vendor's, which serves only the GLKVM UI on 443.

The repository is the recovery source even if a firmware update wipes both
`/etc/kvmd/user` and `/etc/kvmd`.

### Bootstrap after a wipe

Restore the appliance's network configuration and SSH access through the vendor UI first. Confirm `ssh root@10.32.20.10` works; this firmware uses Dropbear, so the `scp -O` commands in **Apply** are intentional. Then run the complete **Apply** procedure from a repository checkout. It recreates the user directories, installs all three HID tools, restores the committed appliance override and the installed firmware's stock nginx configuration, and restarts the affected services. Finish with the non-interactive checks and safe live-input test above, then install the [monitoring](#monitoring) export patch.

Do not run `kvmd` itself over SSH, including `kvmd -M` to validate configuration. Unlike upstream PiKVM, firmware `V1.9.1 release1` started another daemon instead of performing a validation-only run.

## Recovery

For a missing keyboard or hung channel switches with USB class `EF/02/01`, use **Apply** to install the camera-disable override and rebuild the USB gadget. The kvmd-only restarts below recover the daemon's configuration; they do not change USB descriptors. If gadget teardown fails or times out, stop and inspect its log and remaining configfs state; the Apply exit trap restarts kvmd if it is not running, but cannot repair a partially removed USB gadget.

If `kvmd` does not start and the appliance backup survived, restore it:

```sh
ssh root@10.32.20.10 '
    cp /etc/kvmd/user/backups/override.yaml.before-kvm-controls \
        /etc/kvmd/override.yaml
    /etc/init.d/S98kvmd restart
'
```

That backup may predate the camera-disable fix. Before another appliance reboot or USB rebuild, run **Apply** or ensure the restored override contains `otg.devices.camera.enabled: false`. A kvmd restart alone does not update the USB descriptors. Verify the live class bytes and actual input after the next reboot.

If the appliance backup did not survive, upload the committed override instead:

```sh
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml
ssh root@10.32.20.10 '
    cp /tmp/override.yaml /etc/kvmd/override.yaml
    rm -f /tmp/override.yaml
    /etc/init.d/S98kvmd restart
'
```

If nginx does not start, restore the vendor configuration:

```sh
ssh root@10.32.20.10 '
    cp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    /etc/init.d/S99kvmd-nginx restart
'
```

The helpers live on the persistent user partition. Firmware upgrades may still replace `/etc/kvmd/override.yaml` or change the vendor nginx configuration. After an upgrade, keep the new firmware's stock nginx file and recheck the USB compatibility and monitoring overrides before restoring them.
