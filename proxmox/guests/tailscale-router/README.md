# Tailscale subnet-router guests

Two small Debian 13 VMs provide a cluster-independent Tailscale ingress path. They are single-homed on VLAN 19 and run on separate PVE nodes:

| VMID | Name | PVE node | Address |
|---|---|---|---|
| 101 | `tailscale-router-1` | `pve-sbx-2` | `10.32.19.101/24` |
| 102 | `tailscale-router-2` | `pve-sbx-3` | `10.32.19.102/24` |

Each VM has 1 vCPU, 1 GiB RAM, an 8 GiB `local-lvm` disk, one VirtIO NIC on `vmbr0` tagged 19, and PVE guest-agent support. They boot with their host, are protected against accidental deletion, and are included in `daily-backups`; they are not PVE HA resources.

[`vendor-data.yaml`](vendor-data.yaml) installs Tailscale from its official Debian 13 repository, enables forwarding, installs the guest agent, and persists Tailscale's recommended UDP GRO forwarding settings. It contains no credentials, auth keys, ACLs, or advertised routes.

## Provisioning

The shared snippet is stored as `library-pve:snippets/tailscale-router.yaml`. VM creation clones the Debian 13 cloud image template (VMID 9000), applies the static address and VLAN tag, and attaches that vendor data.

After first boot, validate:

```sh
cloud-init status --wait
systemctl --failed
systemctl is-active qemu-guest-agent tailscaled tailscale-gro-forwarding
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
ip route
ethtool -k ens18 | grep -E 'rx-udp-gro-forwarding|rx-gro-list'
```

The live Tailnet configuration is:

```sh
sudo tailscale up --accept-dns=false --accept-routes=false --advertise-tags=tag:remote-admin-router --hostname=<node-name> --operator=kelchm --snat-subnet-routes=true
sudo tailscale set --advertise-routes=10.32.0.0/16
```

Both machines have non-expiring keys, are tagged `tag:remote-admin-router`, keep subnet-route SNAT enabled, do not accept peer routes or Tailnet DNS, and advertise the exact same `10.32.0.0/16`. Tailscale selects one router for the route and moves it to the other after failure; selection can remain on the survivor after recovery, so neither VM has a permanent primary role. Authentication remains an interactive/manual operation; do not put auth keys into Git or cloud-init.

## Validation and failover

The aggregate route is approved only for `tag:remote-admin-router` in the Tailnet policy. UniFi independently permits only `.101/.102` to the six documented destination prefixes and generates an established/related return rule; all other Remote Admin → Internal initiation remains denied.

The 2026-09-02 acceptance drill enabled routes on an enrolled Main client and confirmed that its connected `10.32.10.0/24` remained physical while other destinations in the aggregate used Tailscale. PVE, the Kubernetes API, both Traefik VIPs, and split DNS passed; Workloads and Storage hosts remained blocked by UniFi. Stopping each selected router in turn moved the `/16` to its peer in about 16 seconds with every positive probe still passing. Both daemons were restored and the client returned to `--accept-routes=false` for normal at-home use. Only the connected `/24` is more specific than the aggregate; every other home VLAN is captured by a connected at-home client, so clients disconnect on home Wi-Fi per the [client location policy](../../../docs/plans/20260902-tailscale-remote-admin.md#client-location-policy). Full policy and rollback details are in the [remote-admin plan](../../../docs/plans/20260902-tailscale-remote-admin.md).
