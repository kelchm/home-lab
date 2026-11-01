# TRMNL server trials

These are temporary, independent deployments for choosing a self-hosted TRMNL server. Trial lifecycle and the eventual selection/removal are tracked in [#1219](https://github.com/kelchm/home-lab/issues/1219). The existing Visionect and Broadsheet services continue separately.

## Open a trial

Use the home LAN or a VPN route to the admin network. Each URL uses the private admin Gateway and the existing `*.home.kelch.io` certificate; these servers have no public route or external DNS record. They use their own application login rather than the homelab OIDC layer, so device APIs can work without browser SSO.

| Server | URL | First visit |
|---|---|---|
| Terminus | <https://terminus.home.kelch.io> | Register an account. The first account is verified automatically. |
| LaraPaper | <https://larapaper.home.kelch.io> | Register an account; production mode does not seed the development admin account. |
| Inker | <https://inker.home.kelch.io> | Sign in with the generated trial PIN, retrieved below. |
| BYOS Next.js | <https://byos-next.home.kelch.io/setup> | Create the first administrator; the trial database is initialized. |
| FastAPI | <https://byos-fastapi.home.kelch.io> | Lightweight dashboard; no application login. |
| Django | <https://byos-django.home.kelch.io/admin/> | Sign in as `trial` using the generated password below. |
| Phoenix | <https://byos-phoenix.home.kelch.io> | Device management and code-defined screens; no application login. |
| Node Lite | <https://node-lite.home.kelch.io> | The root is intentionally empty. Use the image/API commands below. |

Retrieve the Inker PIN and Django password from the cluster; it is also stored encrypted in the trial Secret manifest:

```sh
kubectl -n trmnl-trials get secret inker-trial -o jsonpath='{.data.ADMIN_PIN}' | base64 --decode
kubectl -n trmnl-trials get secret byos-django-trial -o jsonpath='{.data.DJANGO_SUPERUSER_PASSWORD}' | base64 --decode
```

The stateful applications use separate Longhorn volumes; Node Lite is stateless and its screen/template implementation lives in its pinned source image. Terminus and Next.js each have a dedicated PostgreSQL container within their own pod; Terminus also has its own worker and persistent Valkey queue. There are no shared application accounts, databases, API keys or playlists. Credentials and application encryption keys are SOPS encrypted in Git. RWO volumes use one replica and `Recreate` updates. PVCs are retained when a release is removed, so removing a trial does not silently erase its data.

## Compare with the same task

Start in Terminus, then repeat the workflow in each candidate where it has the corresponding feature:

1. Explore device registration, screen previews, recipes/plugins and playlist controls.
2. Make a simple screen with a clock and a short text message. Import or configure one recipe you would actually use, without adding sensitive credentials during initial exploration.
3. Check the generated preview and try a two-screen playlist. Note which features are available locally and which expect the hosted TRMNL service or a separate cloud license.
4. If testing hardware, point one physical device at one server at a time. Verify that it receives the image, advances the playlist and reports its battery/Wi-Fi status. Browser previews alone do not prove device compatibility.
5. Record the preferred server and anything worth exporting in #1219. Selection is based on the workflows you use, rather than the implementation language.

The servers can fetch public HTTP/HTTPS recipes and pages. Their network policy blocks direct connections to other private homelab services, and their database/cache ports have no Service. Rendering a private Grafana dashboard or another internal page needs an explicit additional egress decision.

## Physical device

Follow the [TRMNL device connection guide](https://help.trmnl.com/en/articles/12263392-connect-your-device-to-terminus-byos). Before changing a device, record its current custom server, API key, account association and Wi-Fi configuration so it can be restored. Unknown-device responses in some implementations include `reset_firmware: true`; switching servers or a failed enrollment may clear the saved credentials. Use the matching server URL above as the custom host, without `/setup` or another path. Terminus's `API_URI`, LaraPaper's `APP_URL` and Next.js's `BETTER_AUTH_URL` already match their trial URLs.

The trial disables automatic stock-firmware offers: Terminus has its catalog synchronizer disabled and a pinned default-device override with updates off; the Next.js preload guard blocks only its stock firmware release/CDN fetches. Existing cached Terminus firmware entries were removed before enrollment checks. Keep custom firmware on the boards during this comparison.

A device must resolve the private hostnames and reach `10.32.130.1:443`. An IoT VLAN firewall rule may be required separately from the Kubernetes policies; do not change existing VLAN access until the device's source network is known. Device enrollment and TLS support must be verified on the actual firmware. A browser-accessible UI is not a hardware acceptance check.

## Enrollment differences

- **Terminus:** `/api/setup` can provision a new MAC and its welcome playlist. Send a recognized `Model` (for example `og`) and a semantic `FW-Version`. The trial defaults firmware updates off.
- **LaraPaper:** register the first user, then enable Permit Auto-Join or create the device manually. Configure its model and dimensions before using the display endpoint.
- **Inker:** `/api/setup` auto-provisions the MAC and recognizes model/width/height headers. Use the returned API key as `Access-Token`.
- **Next.js:** the deployed trial has its database initialized and built-in recipes seeded. Create the first administrator at `/setup`, then add/claim the device through the authenticated UI. For a fresh empty database, initialize it at `/setup` and restart the pod once before adding devices. An unauthenticated device cannot become an owned device by itself.
- **FastAPI:** `/api/setup` returns its configured setup key, and the minimal UI manages device/playlist state. Some example plugins target the upstream author's private services and need configuration; public example outputs are available without those services.
- **Django:** `/api/setup` auto-registers a new MAC. Assign that device to the `trial` user in the admin UI and add a Screen before expecting custom content. Upstream setup returns 404 once a device already has a user, so preserve the initial API key for reconfiguration. The trial wrapper normalizes the slashless setup/display paths internally, avoiding a 301 that firmware may not follow.
- **Phoenix:** create the device in the UI first using its exact MAC, API key and friendly ID. Then call `/api/setup`; an unregistered MAC returns 404. Rendering starts after device creation, so test a generated BMP as well as the management page.
- **Node Lite:** configure the one permitted MAC in its encrypted Secret, then use its setup/display API and authenticated image endpoint. Its root page is intentionally blank.

## Candidate coverage

The [TRMNL BYOS matrix](https://docs.trmnl.com/go/diy/byos) and [ecosystem](https://trmnl.com/ecosystem) identify the implementations. The requested scope is all eight, including the experimental implementations, using physical custom devices. Terminus, LaraPaper, Inker and BYOS Next.js use upstream images. Their upstream images were inspected and pinned on 2026-10-31; the image pins are in [the manifests](../../kubernetes/apps/trmnl-trials/app). The published Next.js image is built from `7d6615d15925f710b6b3d57f2978839e61d14c40`, rather than assuming the latest source tree matches the released container.

FastAPI, Django, Phoenix and Node Lite use locally built amd64 trial images from verified upstream commit archives. [Build inputs](../../tools/trmnl-trials/sources.json) pin each archive and its SHA-256; [the packaging](../../tools/trmnl-trials/images) pins base images and Python/npm dependencies. Build a candidate with `python3 tools/trmnl-trials/build.py fastapi --docker-context <context>` (substitute `django`, `phoenix`, or `node-lite`). Registry image digests are pinned in the HelmReleases after publication. The OSS trial images are publicly pullable GHCR packages. Application pods need no registry credential and do not mount a Kubernetes API token. [The build record](../../tools/trmnl-trials/images.lock.json) identifies the exact published digests.

These implementations differ substantially. Django uses its native admin interface and upstream renders at 800×480. Phoenix stores devices in SQLite and generates screens from Elixir modules; the packaging supplies browser/image tools and the upstream application runs its migrations on startup. FastAPI has a minimal UI and plugin-driven image generation. Neither FastAPI nor Phoenix adds an application login, so private network access is the access boundary.

Node Lite has no management UI, playlists or database. Upstream ships BYOS disabled as a source constant; the trial packaging enables the existing BYOS code through an environment variable, sets the timezone from `TZ`, and disables cloud firmware updates. It still permits only one MAC address and uses a single rendered screen. The configured MAC is the synthetic test address `02:00:00:00:00:08`; replace `BYOS_DEVICE_MAC` in the encrypted Node Lite Secret with the physical device's actual `ID` header before enrollment. Node Lite is limited to 800×480: upstream hardcodes the layout and BMP conversion dimensions, so changing only the browser viewport is insufficient. The trial also removes its startup log of the authenticated image URL. Its authenticated image endpoint is `/image?secret_key=<SECRET_KEY>`; the custom-device endpoints are `/api/setup` and `/api/display` with the configured `ID` and `Access-Token` headers. Retrieve `SECRET_KEY`, `BYOS_DEVICE_ACCESS_TOKEN` or `BYOS_DEVICE_MAC` with the same exact-key Secret command used above; avoid putting authenticated URLs into shared logs.

The FastAPI, Django and Phoenix implementations are marked unmaintained in the upstream BYOS matrix. They remain included for this trial so their actual behavior and limitations can inform the choice. Custom firmware and panel dimensions are still needed before claiming hardware compatibility.

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

On 2026-11-01, all eight HelmReleases and Deployments became Ready in `trmnl-trials`; all eight HTTPRoutes were accepted with resolved backend references. Every trial URL in the table served its expected page over HTTPS (Node Lite serves an intentionally empty root). Deployment was initially staged from this branch for live acceptance; the PR records the GitOps adoption revision.

Synthetic device setup/display/image requests succeeded through HTTPS for Terminus and Inker (800×480 PNG) and FastAPI, Django, Phoenix and Node Lite (800×480 BMP). Node Lite returned a 1-bit BMP. Django generated a screen through Firefox/ImageMagick and served it through the slashless firmware API; Phoenix generated its Hello World screen through Chromium/ImageMagick and served it through its device API. Synthetic device MACs use `02:00:00:00:00:01`, `:03`, `:05`, `:06`, `:07`, and `:08`, respectively; these are trial data, not registered physical boards.

Next.js initialized all 21 migrations through its own setup action, restarted with the database wait, and seeded 11 built-in recipes. Its first administrator and LaraPaper's first user are left for the owner to register. Those two owned-device flows still need acceptance after account setup. The stock-firmware guard was checked in the Next.js container, and Terminus setup/display returned firmware update/reset flags false. A private-LAN HTTPS connection attempt from a trial pod was blocked. All four custom image digests were anonymously pullable, and no registry pull credential remains in the manifests or namespace.

The eight charts passed rendering and Kubernetes API dry-run validation; Flate passed all 185 resources, and local documentation links and SOPS encryption checks passed. Physical-board acceptance, custom panel/firmware compatibility, and any necessary IoT VLAN access remain open in #1219. The completed server rollout does not imply that a physical board has been enrolled.
