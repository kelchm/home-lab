# TRMNL server trials

These are four temporary, independent deployments for choosing a self-hosted TRMNL server: Terminus, LaraPaper, Inker and BYOS Next.js. Trial lifecycle and the eventual selection/removal are tracked in [#1219](https://github.com/kelchm/home-lab/issues/1219). The existing Visionect and Broadsheet services continue separately.

## Open a trial

Use the home LAN or a VPN route to the admin network. Each URL uses the private admin Gateway and the existing `*.home.kelch.io` certificate; these servers have no public route or external DNS record. They use their own application login rather than the homelab OIDC layer, so device APIs can work without browser SSO.

| Server | URL | First visit |
|---|---|---|
| Terminus | <https://terminus.home.kelch.io> | Register an account. The first account is verified automatically. |
| LaraPaper | <https://larapaper.home.kelch.io> | Register an account; production mode does not seed the development admin account. |
| Inker | <https://inker.home.kelch.io> | Sign in with the generated trial PIN, retrieved below. |
| BYOS Next.js | <https://byos-next.home.kelch.io/setup> | Create the first administrator; the trial database is initialized. |

Retrieve the Inker PIN from the cluster; it is also stored encrypted in the trial Secret manifest:

```sh
kubectl -n trmnl-trials get secret inker-trial -o jsonpath='{.data.ADMIN_PIN}' | base64 --decode
```

The applications use separate Longhorn volumes. Terminus and Next.js each have a dedicated PostgreSQL container within their own pod; Terminus also has its own worker and persistent Valkey queue. There are no shared application accounts, databases, API keys or playlists. Credentials and application encryption keys are SOPS encrypted in Git. RWO volumes use one replica and `Recreate` updates. PVCs are retained when a release is removed, so removing a trial does not silently erase its data.

## Compare with the same task

Start in Terminus, then repeat the workflow in each candidate where it has the corresponding feature:

1. Explore device registration, screen previews, recipes/plugins and playlist controls.
2. Make a simple screen with a clock and a short text message. Import or configure one recipe you would actually use, without adding sensitive credentials during initial exploration.
3. Check the generated preview and try a two-screen playlist. Note which features are available locally and which expect the hosted TRMNL service or a separate cloud license.
4. If testing hardware, point one physical device at one server at a time. Verify that it receives the image, advances the playlist and reports its battery/Wi-Fi status. Browser previews alone do not prove device compatibility.
5. Record the preferred server and anything worth exporting in #1219. Selection is based on the workflows you use, rather than the implementation language.

The servers can fetch public HTTP/HTTPS recipes and pages. Their network policy blocks connections to private homelab services, including the shared admin Gateway, and their database/cache ports have no Service. Rendering a private Grafana dashboard or another internal page needs an explicit additional egress decision.

## Physical device

Follow the [TRMNL device connection guide](https://help.trmnl.com/en/articles/12263392-connect-your-device-to-terminus-byos). Before changing a device, record its current custom server, API key, account association and Wi-Fi configuration so it can be restored. Unknown-device responses in some implementations include `reset_firmware: true`; switching servers or a failed enrollment may clear the saved credentials. Use the matching server URL above as the custom host, without `/setup` or another path. Terminus's `API_URI`, LaraPaper's `APP_URL` and Next.js's `BETTER_AUTH_URL` already match their trial URLs.

The trial disables automatic stock-firmware offers: Terminus has its catalog synchronizer disabled and a pinned default-device override with updates off; the Next.js preload guard blocks only its stock firmware release/CDN fetches. Existing cached Terminus firmware entries were removed before enrollment checks. Keep custom firmware on the boards during this comparison.

A device must resolve the private hostnames and reach `10.32.130.1:443`. An IoT VLAN firewall rule may be required separately from the Kubernetes policies; do not change existing VLAN access until the device's source network is known. Device enrollment and TLS support must be verified on the actual firmware. A browser-accessible UI is not a hardware acceptance check.

## Enrollment differences

- **Terminus:** `/api/setup` can provision a new MAC and its welcome playlist. Send a recognized `Model` (for example `og`) and a semantic `FW-Version`. The trial defaults firmware updates off.
- **LaraPaper:** register the first user, then enable Permit Auto-Join or create the device manually. Configure its model and dimensions before using the display endpoint.
- **Inker:** `/api/setup` auto-provisions the MAC and recognizes model/width/height headers. Use the returned API key as `Access-Token`.
- **Next.js:** the deployed trial has its database initialized and built-in recipes seeded. Create the first administrator at `/setup`, then add/claim the device through the authenticated UI. For a fresh empty database, initialize it at `/setup` and restart the pod once before adding devices. An unauthenticated device cannot become an owned device by itself.

## Candidate selection

The [TRMNL BYOS matrix](https://docs.trmnl.com/go/diy/byos) lists Terminus, LaraPaper, Inker and BYOS Next.js as maintained implementations. These four trials use upstream images pinned in [the manifests](../../kubernetes/apps/trmnl-trials/app), with no local image builder or vendored application dependency locks. The published Next.js image is built from `7d6615d15925f710b6b3d57f2978839e61d14c40`; its released image must not be assumed to match the latest source tree.

The initial evaluation also included BYOS FastAPI, BYOS Django, BYOS Phoenix and Node Lite. The owner chose to drop those four on 2026-11-01. The first three are marked unmaintained in the upstream matrix. [Node Lite](https://github.com/usetrmnl/byos_node_lite#readme) is a code-driven image-server starter with basic BYOS support for one device and no dashboard, rather than a comparable ready-to-use management server. Their custom packaging and dependency locks have been removed from the repository; Renovate policy is unchanged.

### Retained data from retired trials

Retirement removes the four HelmReleases and their workloads, Services and HTTPRoutes. It preserves the four PVCs below, whose `helm.sh/resource-policy: keep` annotations were verified before retirement, and their corresponding encrypted application Secrets. Node Lite has no persistent volume.

| Retired server | Retained PVCs | Retained Secret |
|---|---|---|
| BYOS FastAPI | `byos-fastapi` | `byos-fastapi-trial` |
| BYOS Django | `byos-django` | `byos-django-trial` |
| BYOS Phoenix | `byos-phoenix-data`, `byos-phoenix-generated` | `byos-phoenix-trial` |
| Node Lite | None | `node-lite-trial` |

The removed manifests, builder and image digests remain available in Git at [the initial deployment revision](https://github.com/kelchm/home-lab/tree/f8687927e4f648ae818ce9b9f035a2dcda676d0b). Restoring a retired server requires restoring its configuration and checking compatibility with its retained data. Deleting retained PVCs or Secrets is a separate decision; retirement does not erase trial data.

## Check and stop trials

```sh
flux reconcile kustomization trmnl-trials -n trmnl-trials --with-source
kubectl -n trmnl-trials get helmrelease,pods,pvc,httproute
kubectl -n trmnl-trials logs deployment/terminus -c app --tail=80
kubectl -n trmnl-trials logs deployment/terminus -c worker --tail=80
```

Check that all HTTPRoutes have `Accepted=True` and `ResolvedRefs=True`, the workloads are Ready, `/up` responds for Terminus and LaraPaper, `/health` responds for Inker, and Next.js serves its setup/login page. After Next.js database initialization, verify that the database has no pending migrations, then restart its pod once so the one-time built-in recipe sync runs against the initialized schema. Its startup command waits for PostgreSQL to accept TCP connections on subsequent boots. An app process and a healthy database container alone are not sufficient evidence that screen generation works.

To pause one server without deleting its state, commit `controllers.main.replicas: 0` in that server's HelmRelease. To resume, restore it to `1`. Do not scale imperatively while Flux continues to manage the value.

After the owner chooses a winner, export useful settings/images, then remove unwanted HelmRelease references and manifests from the app Kustomization. Keep their Secrets and retained PVCs until data deletion is explicitly wanted. For complete retirement, remove the namespace's `ks.yaml` reference first so Flux uninstalls the Helm releases and removes routes/policy, remove the TRMNL allowance from the Traefik admin policy, and then remove the remaining trial configuration. Namespace pruning is disabled and Helm PVC retention is enabled; explicitly deleting the namespace or PVCs destroys trial data and needs a deliberate final decision. A rollback to a previous application image may not reverse database migrations.

## Verification record

The initial eight-server deployment was adopted by Flux on 2026-11-01 at `f8687927e4f648ae818ce9b9f035a2dcda676d0b`. All eight releases and private routes were healthy. Synthetic device setup/display/image requests succeeded through HTTPS for Terminus and Inker (800×480 PNG) and the four subsequently retired experimental servers (800×480 BMP). Those results established basic protocol and rendering behavior, rather than project maturity or physical-board compatibility.

For the remaining candidates, Terminus and Inker have synthetic devices at `02:00:00:00:00:01` and `02:00:00:00:00:03`. Next.js initialized all 21 migrations and seeded 11 built-in recipes. Its first administrator and LaraPaper's first user are left for the owner to register; their owned-device flows still need acceptance after account setup. The stock-firmware guard was checked in the Next.js container, and Terminus setup/display returned firmware update/reset flags false. Private-LAN and shared admin Gateway HTTPS connection attempts from a trial pod were blocked.

Physical-board acceptance, custom panel/firmware compatibility, and any necessary IoT VLAN access remain open in #1219. The completed server rollout does not imply that a physical board has been enrolled.
