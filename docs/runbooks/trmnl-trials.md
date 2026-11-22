# TRMNL server trials

These are four temporary, independent deployments for choosing a self-hosted TRMNL server: Terminus, LaraPaper, Inker and BYOS Next.js. Trial lifecycle and the eventual selection/removal are tracked in [#1219](https://github.com/kelchm/home-lab/issues/1219). The existing Visionect and Broadsheet services continue separately.

## Open a trial

Use the home LAN or a VPN route to the admin network. Each URL uses the private admin Gateway and the existing `*.home.kelch.io` certificate; these servers have no public route or external DNS record. They use their own application login rather than the homelab OIDC layer, so device APIs can work without browser SSO.

| Server | URL | 1Password Login item |
|---|---|---|
| Terminus | <https://terminus.home.kelch.io> | `TRMNL - Terminus` |
| LaraPaper | <https://larapaper.home.kelch.io> | `TRMNL - LaraPaper` |
| Inker | <https://inker.home.kelch.io> | `TRMNL - Inker` |
| BYOS Next.js | <https://byos-next.home.kelch.io/sign-in> | `TRMNL - Next.js` |

All four Login items are in the **Project - home-lab** vault in 1Password. Inker uses its password field as the administrator PIN; the other items contain the email/password accounts created through each application's registration flow. The saved credentials were read back from 1Password and used to verify fresh sign-ins on 2026-11-08. No browser passwords are stored in Git.

The Inker PIN also remains in Kubernetes Secret `trmnl-trials/inker-trial`, key `ADMIN_PIN`, and in the encrypted trial Secret manifest. Application encryption keys and database credentials remain SOPS encrypted in Git.

The applications use separate Longhorn volumes. Terminus and Next.js each have a dedicated PostgreSQL container within their own pod; Terminus also has its own worker and persistent Valkey queue. There are no shared application accounts, databases, API keys or playlists. RWO volumes use one replica and `Recreate` updates. PVCs are retained when a release is removed, so removing a trial does not silently erase its data.

## Compare with the same task

Start in Terminus, then repeat the workflow in each candidate where it has the corresponding feature:

1. Explore device registration, screen previews, recipes/plugins and playlist controls.
2. Make a simple screen with a clock and a short text message. Import or configure one recipe you would actually use, without adding sensitive credentials during initial exploration.
3. Check the generated preview and try a two-screen playlist. Note which features are available locally and which expect the hosted TRMNL service or a separate cloud license.
4. If testing hardware, point one physical device at one server at a time. Verify that it receives the image, advances the playlist and reports its battery/Wi-Fi status. Browser previews alone do not prove device compatibility.
5. Record the preferred server and anything worth exporting in #1219. Selection is based on the workflows you use, rather than the implementation language.

The trial namespace has no Kubernetes NetworkPolicy or CiliumNetworkPolicy restrictions. The servers can fetch public recipes and private homelab services, including the shared admin Gateway, subject to destination policies and application authentication. Their database/cache ports have no Service. The admin Gateway retains its backend allowance so the trial URLs continue to work.

## Grafana in Inker

The installed Grafana plugin has a connection named **Homelab Grafana**, pointing at <https://grafana.home.kelch.io>. It uses the dedicated `trmnl-inker` Grafana service account with the **Viewer** role. Its token is stored in the **Grafana - Inker** API Credential item in the **Project - home-lab** 1Password vault, in the `credential` field. Inker stores its copy in the connection's encrypted settings on its persistent volume. Its **Allow Local Network** setting is enabled for rendering the private Grafana URL.

On 2026-11-08, the connection successfully listed 95 dashboards and the panels in the Home dashboard, then rendered the Home dashboard's **Needs attention** panel as an 800×480 PNG. The temporary verification screen was removed. Open the Grafana connection in Inker to choose a dashboard and panel or section for a screen.

The token has no expiration. To rotate it, create a replacement token under Grafana's `trmnl-inker` service account, update the 1Password item and Inker connection, verify rendering, then revoke the old token. No Grafana administrator credential is stored in Inker.

Inker's memory request is 512 MiB and its limit is 4 GiB to accommodate concurrent Chromium renders for Grafana section previews. The original 1 GiB limit caused out-of-memory kills while previewing the Home dashboard's **Everything** section. Inker keeps login sessions in memory, so a backend restart also logs users out. If screen creation fails and the browser returns to login, check the pod's restart count and last termination reason before changing the Grafana credentials.

### E-paper overview

[Home · E-paper](https://grafana.home.kelch.io/d/home-eink?theme=light&kiosk) is a separate 1600×1200 overview of the entire lab using four gray tones. Seven sections cover Kubernetes, network, Sparks, Proxmox, Athena, power and devices with current metrics, 24-hour charts and capacity or runtime bars. The headline and section states use Home's existing health definitions. The attention area shows the three worst current issues and any remaining count, including alerts outside the seven systems; while healthy it shows how long the lab has been free of warnings and the latest warning or critical alert within seven days. Advisory states use light gray, warnings dark gray and critical states black. A timestamp and a seven-day alert strip provide freshness and history; darker shades indicate higher severity.

The source is [home/eink.py](../../scripts/grafana/home/eink.py), which reuses Home's query definitions. Regenerate [home-eink-dashboard.json](../../kubernetes/apps/observability/grafana/app/home-eink-dashboard.json) with `python3 scripts/grafana/render-home.py`. The existing Home dashboard CI check verifies both generated files, shared health definitions, query length and the fixed page layout.

Inker's **Home lab · E-paper** screen (instance `9`) captures the **whole dashboard** (`panel_id: full`) at 1600×1200. Use whole-dashboard capture instead of the section compositor, which rearranges panels into equal-sized cells. The [image](https://inker.home.kelch.io/api/plugins/instances/9/render?mode=preview) requires no application login and is reachable through the private admin Gateway. Use `mode=preview` to preserve gray tones; `mode=einkPreview` converts to black and white. Preview mode also bypasses Inker's 90 kB downscaling limit. On 2026-11-08, a real grayscale Inker render, including an advisory state, was visually verified at 1600×1200: it took about 4.2 seconds and produced a 209,769-byte PNG without restarting Inker. Warning and critical cases were previously checked in a separate local Grafana instance; their grayscale text contrast was checked against Grafana's rendering code. Physical-display acceptance remains open.

For VSS, use `http://inker.trmnl-trials.svc.cluster.local/api/plugins/instances/9/render?mode=preview`. This stays inside the cluster and uses the Visionect policy's explicit Inker service allowance on TCP/80, limited to GET requests for instance-render paths. The admin-Gateway hostname remains blocked by VSS's egress policy. If Inker is retired, remove the VSS allowance and update any VSS sessions using this image URL.

On 2026-11-08, Flux applied [PR #1384](https://github.com/kelchm/home-lab/pull/1384) at `f56e98c91fb93b507504e34d89e89d7be66c5d58`. Grafana adopted the dashboard through file provisioning (`Overview/home-eink.json`). An anonymous fetch from the VSS app container returned HTTP 200 with a 1600×1200 PNG (206,604 bytes) in about 4.1 seconds; GET requests to `/health` and `/api/plugins/instances` returned HTTP 403. Inker remained Ready with zero restarts. These checks verify server connectivity and image generation; physical-display acceptance remains open.

The VSS session must also save the internal service URL in `Backend.Fields.url`; deploying the access rule does not rewrite existing sessions. The trial session on `pnp13-display-3` still used the admin-Gateway URL with `mode=einkPreview` and timed out after the rule was deployed. On 2026-11-08, that session was updated through the VSS session API to the internal URL with `mode=preview`. WebKit finished loading it, VSS pushed the image, and the live-view API's cached and device images matched byte for byte (1600×1200, 74,207 bytes). This verifies the actual VSS rendering and delivery path, beyond the earlier container-only fetch. The session's automatic page reload remains disabled; a static image URL needs [automatic page reload](https://docs.visionect.com/VisionectSoftwareSuite/ManagementInterface.html#other-important-settings) configured for periodic updates.

## Physical device

Follow the [TRMNL device connection guide](https://help.trmnl.com/en/articles/12263392-connect-your-device-to-terminus-byos). Before changing a device, record its current custom server, API key, account association and Wi-Fi configuration so it can be restored. Unknown-device responses in some implementations include `reset_firmware: true`; switching servers or a failed enrollment may clear the saved credentials. Use the matching server URL above as the custom host, without `/setup` or another path. Terminus's `API_URI`, LaraPaper's `APP_URL` and Next.js's `BETTER_AUTH_URL` already match their trial URLs.

The trial disables automatic stock-firmware offers: Terminus has its catalog synchronizer disabled and a pinned default-device override with updates off; the Next.js preload guard blocks only its stock firmware release/CDN fetches. Existing cached Terminus firmware entries were removed before enrollment checks. Keep custom firmware on the boards during this comparison.

A device must resolve the private hostnames and reach `10.32.130.1:443`. An IoT VLAN firewall rule may be required separately from the Kubernetes policies; do not change existing VLAN access until the device's source network is known. Device enrollment and TLS support must be verified on the actual firmware. A browser-accessible UI is not a hardware acceptance check.

## Enrollment differences

- **Terminus:** `/api/setup` can provision a new MAC and its welcome playlist. Send a recognized `Model` (for example `og`) and a semantic `FW-Version`. The trial defaults firmware updates off.
- **LaraPaper:** sign in with the saved account (further signups are closed unless `MULTIUSER_REGISTRATION_ENABLED=true`), then enable Permit Auto-Join or create the device manually. Since 0.46.0, Auto-Join turns itself off after 15 minutes, and re-pairing a device that has connected before needs it on again. Configure its model and dimensions before using the display endpoint.
- **Inker:** `/api/setup` auto-provisions the MAC and recognizes model/width/height headers. Use the returned API key as `Access-Token`.
- **Next.js:** the deployed trial has its database initialized, built-in recipes seeded and an administrator account in 1Password. Sign in, then add/claim the device through the authenticated UI. For a fresh empty database, initialize it and create the first administrator at `/setup`, then restart the pod once before adding devices. An unauthenticated device cannot become an owned device by itself.

## Candidate selection

The [TRMNL BYOS matrix](https://docs.trmnl.com/go/diy/byos) lists Terminus, LaraPaper, Inker and BYOS Next.js as maintained implementations. These four trials use upstream images pinned in [the manifests](../../kubernetes/apps/trmnl-trials/app), with no local image builder or vendored application dependency locks. The published Next.js image is built from `7d6615d15925f710b6b3d57f2978839e61d14c40`; its released image must not be assumed to match the latest source tree.

The initial evaluation also included BYOS FastAPI, BYOS Django, BYOS Phoenix and Node Lite. The owner chose to drop those four on 2026-11-01. The first three are marked unmaintained in the upstream matrix. [Node Lite](https://github.com/usetrmnl/byos_node_lite#readme) is a code-driven image-server starter with basic BYOS support for one device and no dashboard, rather than a comparable ready-to-use management server. Their custom packaging and dependency locks have been removed from the repository; Renovate policy is unchanged.

### Retained data from retired trials

Retirement removed the four HelmReleases and their workloads, Services and HTTPRoutes, and kept their PVCs and encrypted application Secrets. Once detached, the retained volumes were skipped by Longhorn's recurring backups and raised `LonghornVolumeBackupStale`, so on 2026-11-04 the owner deleted the four PVCs (`byos-fastapi`, `byos-django`, `byos-phoenix-data`, `byos-phoenix-generated`), their Longhorn volumes and their backups on the NAS. That data is gone. The Secrets remain:

| Retired server | Retained Secret |
|---|---|
| BYOS FastAPI | `byos-fastapi-trial` |
| BYOS Django | `byos-django-trial` |
| BYOS Phoenix | `byos-phoenix-trial` |
| Node Lite | `node-lite-trial` |

The removed manifests, builder and image digests remain available in Git at [the initial deployment revision](https://github.com/kelchm/home-lab/tree/f8687927e4f648ae818ce9b9f035a2dcda676d0b). Restoring a retired server means redeploying it from that revision with empty volumes. Removing the remaining Secrets is a separate decision.

## Check and stop trials

```sh
flux reconcile kustomization trmnl-trials -n trmnl-trials --with-source
kubectl -n trmnl-trials get helmrelease,pods,pvc,httproute
kubectl -n trmnl-trials logs deployment/terminus -c app --tail=80
kubectl -n trmnl-trials logs deployment/terminus -c worker --tail=80
```

Check that all HTTPRoutes have `Accepted=True` and `ResolvedRefs=True`, the workloads are Ready, `/up` responds for Terminus and LaraPaper, `/health` responds for Inker, and Next.js serves its setup/login page. After Next.js database initialization, verify that the database has no pending migrations, then restart its pod once so the one-time built-in recipe sync runs against the initialized schema. Its startup command waits for PostgreSQL to accept TCP connections on subsequent boots. An app process and a healthy database container alone are not sufficient evidence that screen generation works.

To pause one server without deleting its state, commit `controllers.main.replicas: 0` in that server's HelmRelease. To resume, restore it to `1`. Do not scale imperatively while Flux continues to manage the value.

After the owner chooses a winner, export useful settings/images, then remove unwanted HelmRelease references and manifests from the app Kustomization. Keep their Secrets and retained PVCs until data deletion is explicitly wanted. For complete retirement, remove the namespace's `ks.yaml` reference first so Flux uninstalls the Helm releases and removes routes, remove the TRMNL allowance from the Traefik admin policy, and then remove the remaining trial configuration. Namespace pruning is disabled and Helm PVC retention is enabled; explicitly deleting the namespace or PVCs destroys trial data and needs a deliberate final decision. A rollback to a previous application image may not reverse database migrations.

## PostgreSQL 19 cutover

Terminus and BYOS Next keep their databases in dedicated Longhorn claims. The cutover changes both the image and the claim. PostgreSQL 19 starts only on a restored database and Flux retries failures on the selected claim. Keep the PostgreSQL 18 claims until the owner approves retiring them.

| Trial | PostgreSQL 18 claim | PostgreSQL 19 claim | Smoke test |
| --- | --- | --- | --- |
| Terminus | `terminus-database` | `terminus-pg19` | `/up`, sign in, open an existing device and render a preview |
| BYOS Next | `byos-next` | `byos-next-pg19` | `/sign-in`, sign in, open an existing recipe and render a preview |

On 2026-11-22, isolated copies of the trial databases restored into PostgreSQL 19.0 and back into 18.7 with matching data, sequences and extensions. That rehearsal skipped access-control lists. The first live transfer then stopped on a grant to BYOS Next's separate login role. Both live trials recovered on their original 18 claims. The revised helper restored fresh isolated copies of both live databases with roles and grants included. A disposable fixture also passed a password-authenticated read using the restored application role. Application smoke tests are part of the live acceptance.

### Prepare both databases before merging

Use the reviewed migration branch in an approved window. Both trials are offline until their claims are restored and the cutover PR has merged. Arrange for trial users and devices to stay off during verification. Confirm the production context, both PostgreSQL 18 Deployments and their original claims match the table, and each original volume has a completed Longhorn backup. Record the old claim UIDs and the two HelmRelease revisions. The [new claims](../../kubernetes/apps/trmnl-trials/app/postgres-19-claims.yaml) can be created from the reviewed branch before the HelmRelease changes land:

```sh
kubectl -n trmnl-trials create -f kubernetes/apps/trmnl-trials/app/postgres-19-claims.yaml
kubectl -n trmnl-trials wait --for=jsonpath='{.status.phase}'=Bound pvc/terminus-pg19 pvc/byos-next-pg19 --timeout=120s
```

For each trial, set `app` to its name. Suspend its HelmRelease, wait for any active reconciliation to finish, then scale down the Deployment. Confirm that its Pod, including the Terminus worker, is gone. The [migration helper](../../scripts/trmnl/migrate-postgres-19.sh) checks suspension, zero replicas and that no Pod mounts either claim before starting its transfer Pods. The two database processes listen only on local sockets. The helper streams roles and login verifiers first, then restores the final database dump with its grants. It compares roles, memberships, table contents, sequences and extensions before marking the target restored. It removes its Pods on exit.

```sh
app=terminus
flux -n trmnl-trials suspend helmrelease "$app"
kubectl -n trmnl-trials get helmrelease "$app"
# Wait for active reconciliation to finish before scaling down.
kubectl -n trmnl-trials scale "deployment/$app" --replicas=0
kubectl -n trmnl-trials get pods -l "app.kubernetes.io/instance=$app"
bash scripts/trmnl/migrate-postgres-19.sh "$app"
```

Repeat for `byos-next`. Require a successful transfer for both trials in the same maintenance window, with the 18 Deployments kept at zero and the transfer Pods gone, before merging. If either transfer fails, leave the cutover PR open. Once the helper Pods are gone, bring each stopped 18 Deployment back to one replica and resume its HelmRelease. Restarting an 18 Deployment invalidates its 19 restore, even if that transfer succeeded and wrote a marker. Before retrying, replace every invalidated 19 target with an empty claim and rerun both transfers. Preserve an incomplete claim for diagnosis until the owner approves replacing it.

### Activate and verify

With both restores verified and the HelmReleases suspended, merge the cutover PR and reconcile `trmnl-trials` against the new Git revision. Confirm the two HelmRelease specs reference their new claims and PostgreSQL 19 image, still have `spec.suspend: true`, and use `RetryOnFailure` without rollback remediation. Confirm the old claims still have the same UIDs and the new claims are Bound.

```sh
flux -n flux-system reconcile kustomization cluster-apps --with-source
flux -n trmnl-trials reconcile kustomization trmnl-trials
kubectl -n trmnl-trials get helmrelease terminus byos-next
# Check the applied specs and suspension before resuming either release.
app=terminus
flux -n trmnl-trials resume helmrelease "$app"
flux -n trmnl-trials reconcile helmrelease "$app"
kubectl -n trmnl-trials rollout status "deployment/$app" --timeout=300s
```

Verify the ready container is PostgreSQL 19 on its new claim. Check PostgreSQL and application logs, run the smoke test, and verify the restored devices or recipes are present. Repeat for `byos-next`. Before reopening normal use, verify both apps and their backups, and record the cutover and retained claim UIDs in [the trial issue](https://github.com/kelchm/home-lab/issues/1219).

### Recover

Before normal traffic resumes, suspend the affected trial's HelmRelease and scale its Deployment to zero again. In a reviewed PR, restore the complete pre-cutover database container and persistence values for that trial. This restores the PostgreSQL 18 image, its original claim, and the image's default entrypoint by removing the PostgreSQL 19 `command` and `args` overrides. The original Helm-managed claims have the keep annotation and the new claims are excluded from Flux prune. Before resuming, confirm the original claim UID and PostgreSQL 18 image, and check that the applied HelmRelease omits both database startup overrides. Keep the PostgreSQL 19 claim for diagnosis and verify app health and restored data on the original claim.

Once a trial has accepted writes on PostgreSQL 19, the 18 claim contains the state at the final dump. Preserve the newer claim and get a decision on how to recover those writes before reverting. If rebuilding from a logical backup on a fresh 19 claim, verify the restored data before writing the `.trmnl-pg19-restored` marker.

## Verification record

The initial eight-server deployment was adopted by Flux on 2026-11-01 at `f8687927e4f648ae818ce9b9f035a2dcda676d0b`. All eight releases and private routes were healthy. Synthetic device setup/display/image requests succeeded through HTTPS for Terminus and Inker (800×480 PNG) and the four subsequently retired experimental servers (800×480 BMP). Those results established basic protocol and rendering behavior, rather than project maturity or physical-board compatibility.

For the remaining candidates, Terminus and Inker have synthetic devices at `02:00:00:00:00:01` and `02:00:00:00:00:03`. Next.js initialized all 21 migrations and seeded 11 built-in recipes. The stock-firmware guard was checked in the Next.js container, and Terminus setup/display returned firmware update/reset flags false. Initial isolation tests blocked private-LAN and shared admin Gateway HTTPS connections.

On 2026-11-08, the owner requested removal of the trial network restrictions. Flux applied `e4e1ea0148843a8d0e308cdf86790169afd9e356`; the trial namespace has no NetworkPolicy or CiliumNetworkPolicy resources, and all four application containers reached the private Grafana HTTPS endpoint and received its expected HTTP 307 authentication redirect. All four releases and pods remained Ready. Existing human accounts were absent: Terminus and LaraPaper had no users, and Next.js had only its built-in mono-user placeholder. Fresh trial accounts were registered for those three applications, Next.js's first real user was verified as an administrator, and all four 1Password logins were tested successfully. Owned-device and physical-board flows still need acceptance.

Physical-board acceptance, custom panel/firmware compatibility, and any necessary IoT VLAN access remain open in #1219. The completed server rollout does not imply that a physical board has been enrolled.
