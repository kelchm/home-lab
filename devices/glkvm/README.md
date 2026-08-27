# GL.iNet KVM customizations

Configuration for the GL-RM1PE at `10.32.20.10`. This is manually applied
appliance state; Flux does not reconcile it.

## Web interfaces

[`nginx-kvmd.conf`](nginx-kvmd.conf) makes the bundled upstream PiKVM UI the
primary interface while retaining GL.iNet's UI for firmware and appliance
management:

| Port | Interface |
|------|-----------|
| 443 | PiKVM |
| 8888 | GLKVM |

Port 80 redirects to the primary PiKVM interface. Both HTTPS listeners use the
certificate configured in `/etc/kvmd/user/ssl`.

## Downstream KVM controls

The physical eight-port KVM switches inputs with `Left Ctrl`, `Left Ctrl`,
then the port number. GL.iNet's shortcut UI sends those events too quickly for
the switch. [`kvm-switch.py`](kvm-switch.py) writes standard eight-byte USB HID
keyboard reports with a 75 ms key hold and a 250 ms inter-key delay.

[`override.yaml`](override.yaml) exposes the known hosts as buttons in the
bundled PiKVM UI:

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

All eight downstream ports are assigned and exposed in the UI. Update the table and the corresponding row under `view.table` together if the physical mapping changes.

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

## Apply

The appliance's Dropbear server does not provide an SFTP subsystem, so force
legacy SCP mode with `-O`:

```sh
scp -O devices/glkvm/kvm-switch.py root@10.32.20.10:/tmp/kvm-switch
scp -O devices/glkvm/hid-type.py root@10.32.20.10:/tmp/hid-type
scp -O devices/glkvm/hid-hold-key.py root@10.32.20.10:/tmp/hid-hold-key
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml
scp -O devices/glkvm/nginx-kvmd.conf root@10.32.20.10:/tmp/nginx-kvmd.conf

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
        [ ! -e /etc/kvmd/user/backups/nginx-kvmd.conf.before-pikvm-primary ]; then
        cp /etc/kvmd/nginx-kvmd.conf \
            /etc/kvmd/user/backups/nginx-kvmd.conf.before-pikvm-primary
    fi
    nginx -t -p /etc/kvmd/nginx -c /tmp/nginx-kvmd.conf \
        -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"
    cp /tmp/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    rm -f /tmp/kvm-switch /tmp/hid-type /tmp/hid-hold-key \
        /tmp/override.yaml /tmp/nginx-kvmd.conf
    /etc/init.d/S98kvmd restart
    if [ -f /run/kvmd/pikvm-nginx.pid ]; then
        kill "$(cat /run/kvmd/pikvm-nginx.pid)" 2>/dev/null || true
    fi
    /etc/init.d/S99kvmd-nginx restart
'
```

Confirm `kvmd` restarted and the PiKVM UI responds:

```sh
ssh root@10.32.20.10 '
    /etc/init.d/S98kvmd status
    curl -ksS https://127.0.0.1/login/ | grep -q "PiKVM"
    curl -ksS https://127.0.0.1:8888/ | grep -q "GLKVM"
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

1. Confirm SSH, `/dev/hidg0`, and `/run/kvmd/ustreamer.sock` exist.
2. Compare the vendor nginx configuration with the committed customization;
   do not blindly overwrite a changed vendor schema.
3. Re-run **Apply** to restore the known files and modes.
4. Run the non-interactive checks above and capture a screen.
5. With a host at a safe menu, verify one paced HID key and one downstream KVM
   channel switch.

The repository is the recovery source even if a firmware update wipes both
`/etc/kvmd/user` and `/etc/kvmd`.

### Bootstrap after a wipe

Restore the appliance's network configuration and SSH access through the vendor
UI first. Confirm `ssh root@10.32.20.10` works; this firmware uses Dropbear, so
the `scp -O` commands in **Apply** are intentional. Then run the complete
**Apply** procedure from a repository checkout. It recreates the user
directories, installs all three HID tools, restores the committed PiKVM override
and nginx configuration, and restarts the affected services. Finish with the
non-interactive checks and safe live-input test above.

Do not use `kvmd -M` to validate configuration on this GL.iNet firmware. Unlike
upstream PiKVM, firmware `V1.9.1 release1` starts another daemon instead of
performing a validation-only run.

## Recovery

If `kvmd` does not start and the appliance backup survived, restore it:

```sh
ssh root@10.32.20.10 '
    cp /etc/kvmd/user/backups/override.yaml.before-kvm-controls \
        /etc/kvmd/override.yaml
    /etc/init.d/S98kvmd restart
'
```

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

The script lives on the persistent user partition. Firmware upgrades may still
replace `/etc/kvmd/override.yaml` or change the vendor nginx configuration.
After an upgrade, compare `/etc/kvmd/nginx-kvmd.conf` with
`/rom/etc/kvmd/nginx-kvmd.conf`, then rebase and reapply these customizations.
