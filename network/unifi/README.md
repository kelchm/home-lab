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

Threat Management (Settings → CyberSecure → Threat Management) runs in **Notify** mode — detections are logged and pushed as notifications, nothing is blocked. Suppressions mute a signature; because nothing is blocked, a suppression costs alerting only.

Suppressions are configured from an **alert's own action menu** (System Log → Security → open an event → `Suppress Signature`), not from the `Detection Exclusions` list on the CyberSecure settings page. The two are unrelated: Detection Exclusions take an IP/network/subnet and remove it from analysis entirely, with no way to reference a signature.

**Active suppressions**

| Signature | SID | Scope | Rationale |
|---|---|---|---|
| `ET SCAN Potential SSH Scan OUTBOUND` | 2003068 | Outgoing / Subnet `140.82.112.0/20` | git-over-SSH to GitHub. False positive. |

The ET rule fires on 5 outbound TCP SYNs to port 22 from one source within 120 seconds (`threshold: type threshold, track by_src, count 5, seconds 120`). Routine `git fetch` / `git push` from workstations clears that threshold constantly; unsuppressed it generates ~74 detections/day, ~99% of all Security-category log volume. `140.82.112.0/20` is GitHub's published `git` range from `api.github.com/meta`. Their `git` list also carries `192.30.252.0/22`, `185.199.108.0/22`, and `143.55.64.0/20`; add those as further Subnet rows if detections reappear from outside the /20.

The suppression is scoped rather than global so the signature stays live for every other host and destination — a workstation scanning SSH across the internet still alerts.

### Dialog semantics

`Target: Specific` rows are `Traffic Direction` × `Type` × value, where `Type` is `IP`, `Network`, or `Subnet`. **`Subnet` accepts CIDR**, so a netblock is one row rather than one row per address.

`Traffic Direction` values serialize as `src` / `dest` / `both`, mapping to Suricata's `track by_src` / `by_dst` / `by_either`. The UI label `Outgoing` writes `direction: "dest"` — the listed address is matched as the *destination*. This is worth stating explicitly because the Network app bundle carries an unrelated `incoming`/`outgoing` direction enum used by region blocking, and the two are easy to conflate.

Read back the stored object to confirm what was actually written:

```sh
curl -s -b "TOKEN=$UNIFI_TOKEN" \
  https://unifi.home.kelch.io/proxy/network/api/s/default/rest/setting \
  | jq '.data[] | select(.key=="ips_suppression")'
```

Writes go to `POST .../set/setting/ips_suppression` as a whole-object set — a hand-built POST that omits `whitelist` or carries a stale `_id` clobbers rather than merges. Prefer the UI.

### Verification

Scoped suppression works on this gateway. Applied 2026-08-16 against UniFi OS 5.1.26 / Network 10.5.67, it silenced the signature immediately, and Intrusion Prevention stayed enabled across the change (`ips_mode: "ids"`, 34 categories active).

Two failure modes are widely reported and neither reproduced here: scoped suppressions silently failing to apply, and suppression disabling Intrusion Prevention outright on Network releases before 10.1.83. Both are cheap to re-check after any change to this section. From a host behind the gateway, deliberately cross the rule's threshold:

```sh
seq 6 | xargs -I{} ssh -T -o BatchMode=yes git@github.com
```

No new `Threat Detected` entry in System Log → Security means the suppression is live. If entries do arrive, widen `Target` to `Any` for a single test to separate a broken scope from a broken suppression, then restore `Outgoing` / Subnet `140.82.112.0/20` — leaving `Any` in place would suppress the signature for every host and destination.

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
