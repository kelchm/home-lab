# UniFi network config

Versioned artifacts for UniFi-side configuration that pairs with this repo's
Kubernetes manifests. UniFi is not GitOps-managed; these files are the source
of truth and changes are applied manually.

## Files

- `frr.conf` — BGP peering with the Cilium BGP control plane on the prod Talos
  cluster. AS 65000 (UniFi) ↔ AS 65020 (k8s-prod). See file header for details.
- `bgp-test.yaml` — disposable echo Service for the BGP migration synthetic
  test. Applied via `kubectl`, not Flux. See "Synthetic test" below.

The "Firewall rules" and "IDS/IPS signature suppression" sections below document
intent for configuration that lives only in the UniFi UI (no exportable artifact
lives in this repo).

## Applying `frr.conf`

The config targets FRR, which UniFi gateways (UDM Pro / UDM SE / UXG-series)
ship with. Two paths to apply:

1. **UniFi Network UI (preferred where supported)** — Settings → Routing → BGP.
   Paste the FRR config; the controller reconciles it onto the gateway.
2. **Direct on the gateway** — SSH to the gateway, edit `/etc/frr/frr.conf`,
   `vtysh -c 'configure terminal' -c 'copy running-config startup-config'`.
   Note that UniFi may overwrite manual edits during controller pushes; (1) is
   strongly preferred.

Before pasting, replace `${BGP_PASSWORD}` with the plaintext MD5 password.
Retrieve it with:

```sh
sops --decrypt \
  kubernetes/apps/kube-system/cilium-bgp/app/bgp-secret.sops.yaml \
  | yq '.stringData.password'
```

After applying, validate:

```
show ip bgp summary
show ip bgp neighbors 10.32.30.11
show ip route bgp
```

Sessions should reach `Established` once Cilium is reconciled with matching
peer/auth config. No prefixes are advertised until a `CiliumLoadBalancerIPPool`
matching `admin-prod` or `services-prod` exists and a Service allocates from it.

## Firewall rules

UniFi default inter-VLAN posture is allow, so the BGP LB pool prefixes need
explicit denies from untrusted VLANs. These rules are configured in the
UniFi UI (no committable artifact); this section is the source of truth for
intent.

**Network object:** `bgp-lb-restricted`

| Member          | Notes                                          |
|-----------------|------------------------------------------------|
| `10.32.130.0/24` | `admin-prod` pool                              |
| `10.32.140.0/24` | `services-prod` pool (created in step 9)       |

`shared-prod` (10.32.150.0/24) is intentionally excluded — its tenants need
per-IP+port allow rules from IoT/Guest, not a blanket deny. Add per-service
allows when shared-prod gains a tenant.

**Rules** (Settings → Security → Traffic Rules, or the version-equivalent
LAN-IN section):

| # | Source            | Destination        | Action | Notes                          |
|---|-------------------|--------------------|--------|--------------------------------|
| 1 | IoT (VLAN 90)     | `bgp-lb-restricted` | Drop   | Quieter than reject            |
| 2 | Guest (VLAN 99)   | `bgp-lb-restricted` | Drop   |                                |

VLAN 10 (Main) is intentionally allowed by the default posture and needs no
explicit rule. If/when a more restrictive default-deny posture is adopted
across the network, replace these denies with the corresponding allows from
Main and revisit the per-pool firewall posture in
[`docs/architecture.md`](../../docs/architecture.md#lb-pool-allocation).

Validate by running `curl --max-time 2 http://10.32.130.99/` from a device on
each restricted VLAN — should time out or be refused. The synthetic test
below exercises this as gate 4.

## IDS/IPS signature suppression

Threat Management runs in **Notify** mode, so a suppression costs alerting only. Suppressions are added from an alert's action menu (System Log → Security → open an event → `Suppress Signature`), not from `Detection Exclusions`, which takes an IP/network/subnet and can't reference a signature.

| Signature | SID | Scope | Rationale |
|---|---|---|---|
| `ET SCAN Potential SSH Scan OUTBOUND` | 2003068 | Outgoing / Subnet `140.82.112.0/20` | Routine git-over-SSH trips the rule's 5-SYN-to-port-22-per-120s threshold — ~74 false positives/day, ~99% of Security log volume. |

`140.82.112.0/20` is GitHub's published `git` range (`api.github.com/meta`), which also lists `192.30.252.0/22`, `185.199.108.0/22`, and `143.55.64.0/20` — add those as further Subnet rows if detections reappear. Scoping beats `Target: Any`, which would suppress the signature for every host and destination.

Non-obvious behavior, confirmed by reading `ips_suppression` back from `GET .../rest/setting` after applying:

- `Type: Subnet` accepts CIDR, so a netblock is one row rather than one per address.
- `Traffic Direction` serializes to `src` / `dest` / `both` (Suricata `by_src` / `by_dst` / `by_either`). `Outgoing` writes `direction: "dest"` — not the `incoming`/`outgoing` enum region blocking uses.
- `POST .../set/setting/ips_suppression` is a whole-object set; a hand-built POST that drops `whitelist` clobbers rather than merges. Prefer the UI.

Verified 2026-08-16 on UniFi OS 5.1.26 / Network 10.5.67 — signature silenced, Intrusion Prevention still enabled. To re-check, cross the threshold from a host behind the gateway and confirm no new `Threat Detected` entry appears:

```sh
seq 6 | xargs -I{} ssh -T -o BatchMode=yes git@github.com
```

## Synthetic test (`bgp-test.yaml`)

Step 6 of the BGP migration. Applied manually, not via Flux, so teardown is
trivial. Pins a Service to `10.32.130.99` from the `admin-prod` pool and
exercises the full BGP advertisement / firewall / failover path before any
production cutover.

Apply:

```sh
kubectl apply -f network/unifi/bgp-test.yaml
```

Validate (each step gates the next):

1. **IPAM allocation** — `kubectl -n bgp-test get svc echo` shows
   `EXTERNAL-IP=10.32.130.99`.
2. **BGP advertisement** — on the gateway:
   `vtysh -c 'show ip route 10.32.130.99'` lists 3 ECMP next-hops
   (`10.32.30.11`, `.12`, `.13`).
3. **Allowed-VLAN reachability** — from VLAN 10 (Main):
   `curl -s http://10.32.130.99/` returns the echo JSON.
4. **Denied-VLAN blocking** — from VLAN 90 (IoT) / VLAN 99 (Guest):
   `curl --max-time 2 http://10.32.130.99/` should fail (timeout / refused).
   Requires the rules in "Firewall rules" above to be applied first.
5. **Failover** — `talosctl -n 10.32.30.11 reboot`. From VLAN 10, run a
   continuous `curl` loop; at most one or two requests should fail before
   ECMP reconverges on the remaining 2 next-hops.
   `vtysh -c 'show ip route 10.32.130.99'` should show 2 next-hops during
   the reboot and 3 again after it returns.

Tear down only when all five pass:

```sh
kubectl delete -f network/unifi/bgp-test.yaml
```
