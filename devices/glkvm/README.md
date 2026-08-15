# GL.iNet KVM customizations

Configuration for the GL-RM1PE at `10.32.20.10`. This is manually applied
appliance state; Flux does not reconcile it.

## Downstream KVM controls

The physical eight-port KVM switches inputs with `Left Ctrl`, `Left Ctrl`,
then the port number. GL.iNet's shortcut UI sends those events too quickly for
the switch. [`kvm-switch.py`](kvm-switch.py) writes standard eight-byte USB HID
keyboard reports with a 75 ms key hold and a 250 ms inter-key delay.

[`override.yaml`](override.yaml) exposes the known hosts as buttons in the
bundled PiKVM UI:

| KVM port | Host |
|----------|------|
| 4 | `k8s-prod-1` |
| 5 | `k8s-prod-2` |
| 6 | `k8s-prod-3` |

The script supports ports 1-8. Add another row under `view.table` to expose a
configured channel in the UI.

## Apply

The appliance's Dropbear server does not provide an SFTP subsystem, so force
legacy SCP mode with `-O`:

```sh
scp -O devices/glkvm/kvm-switch.py root@10.32.20.10:/tmp/kvm-switch
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml

ssh root@10.32.20.10 '
    set -eu
    mkdir -p /etc/kvmd/user/bin /etc/kvmd/user/backups
    cp /etc/kvmd/override.yaml \
        /etc/kvmd/user/backups/override.yaml.before-kvm-controls
    cp /tmp/kvm-switch /etc/kvmd/user/bin/kvm-switch
    chmod 0755 /etc/kvmd/user/bin/kvm-switch
    cp /tmp/override.yaml /etc/kvmd/override.yaml
    rm -f /tmp/kvm-switch /tmp/override.yaml
    /etc/init.d/S98kvmd restart
'
```

Confirm `kvmd` restarted and the PiKVM UI responds:

```sh
ssh root@10.32.20.10 '
    /etc/init.d/S98kvmd status
    curl -ksS -o /dev/null -w "%{http_code}\\n" https://127.0.0.1:8888/
'
```

Do not use `kvmd -M` to validate configuration on this GL.iNet firmware. Unlike
upstream PiKVM, firmware `V1.9.1 release1` starts another daemon instead of
performing a validation-only run.

## Recovery

If `kvmd` does not start, restore the saved override:

```sh
ssh root@10.32.20.10 '
    cp /etc/kvmd/user/backups/override.yaml.before-kvm-controls \
        /etc/kvmd/override.yaml
    /etc/init.d/S98kvmd restart
'
```

The script lives on the persistent user partition. Firmware upgrades may still
replace `/etc/kvmd/override.yaml`, so reapply it after an upgrade if the Hosts
menu disappears.
