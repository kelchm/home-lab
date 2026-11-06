# Traefik OIDC plugin startup recovery

## Symptom

OIDC-protected routes on `gateway-admin` return Traefik's plain `404`, while
unprotected routes on the same gateway continue to work. Application pods,
Services, EndpointSlices, HTTPRoutes, and the Gateway can all report healthy.

Typical affected routes include qBittorrent, SABnzbd, the other ARR UIs,
Longhorn, Prometheus, and Alertmanager.

## Cause

`traefik-admin` loads `traefik-oidc-auth` as a Yaegi plugin. Traefik downloads
the pinned plugin once during process startup. If pod DNS, Cilium Service
routing, outbound connectivity, or `plugins.traefik.io` is unavailable at that
moment, Traefik disables plugins and continues running. It does not retry the
download, so every router referencing the missing Middleware is omitted until
the Traefik process restarts.

This occurred after the 2026-08-20 cold boot: a Traefik container attempted the
download at `21:30:58Z`, one second after the first CoreDNS container started
and before the cluster DNS Service path was accepting traffic. Its log showed:

```text
Plugins are disabled because an error has occurred.
unable to install plugin traefik-oidc-auth ... lookup plugins.traefik.io on 10.43.0.10:53 ... connection refused
```

Flux `dependsOn` cannot prevent this race. It orders reconciliation, but
already-created pods restart concurrently after a node or cluster reboot.

## Prevention

The `traefik-admin` HelmRelease enables Traefik's native
`experimental.abortOnPluginFailure` option. If the real plugin download,
integrity check, or Yaegi load fails, Traefik exits nonzero instead of starting
without the plugin. Kubernetes restarts the container and retries the complete
load after its normal container-restart backoff.

This intentionally fails closed. During a full outage `traefik-admin` remains
in `CrashLoopBackOff` instead of silently serving only its unprotected routes.
Once DNS, outbound connectivity, and the plugin catalog recover, the next
container restart loads the plugin and the pod becomes Ready automatically.

The catalog is therefore a startup dependency for `traefik-admin`. An extended
catalog outage, corrupt artifact, or incompatible plugin release keeps the
admin gateway unavailable. Plugin updates remain pinned, manually reviewed,
and never auto-merged.

This option covers plugin installation and build failures. A middleware configuration rejected after the plugin loads can still disable its routers while the process and pod remain healthy. Version 0.22 validates `TokenValidation` and header templates during middleware construction, so inspect router errors and test protected hosts through each replica after an upgrade. A browser-style unauthenticated request (`Accept: text/html`) should redirect to Kanidm; a plain Traefik `404` indicates a missing router. Pod readiness alone does not establish that these routes work.

## Version 0.22 login transition

Version 0.21 keeps the PKCE verifier in a cookie; 0.22 reads it from encrypted OIDC state. A login started on one version and returned to the other fails during a mixed rollout and must be restarted. Existing authenticated sessions remain valid. Use a quiet identity window, wait until both replicas run 0.22, then test fresh arr-suite and ops-suite logins, logout/re-login, and login callbacks crossing replicas. Keep the session-encryption key shared between replicas.

Version 0.22 deployed on 2026-11-06 through [#1040](https://github.com/kelchm/home-lab/pull/1040). Both new replicas logged `Plugins loaded.`, became Ready with zero restarts and redirected all 15 protected hosts to Kanidm when tested individually with browser-style requests. VictoriaLogs was tested at its protected `/select` path; its root redirects to the UI before authentication. The complete startup log window contained no plugin or router errors.

The owner confirmed fresh Sonarr and Longhorn login/re-login. The observed browser callback used one replica; forced cross-replica login/callback and authenticated-session handoffs remain unverified and are tracked in [#1293](https://github.com/kelchm/home-lab/issues/1293).

## Logout and revocation

Signing out of Kanidm does not immediately end each host's gateway session. Our middleware defaults to validating its saved ID token locally, without a per-request Kanidm lookup. `CheckOnEveryRequest` reasserts authorization against that token's claims; it does not fetch current group membership or revocation state. The cookie's configured `MaxAge: 43200` is 12 hours from creation or renewal, not an absolute session lifetime. Token validation or failed refresh can end access earlier. Each host has its own cookie because `SessionCookie.Domain` is unset; sharing an OIDC client and cookie-name prefix does not share host-only cookies.

The plugin's default logout path is `/logout`, but the current Kanidm discovery document has no `end_session_endpoint`. The plugin expects that endpoint and clears its cookie only after a Logout-state callback. Do not advertise this path as a verified logout procedure. The same limitation exists in 0.21. To clear browser access manually, sign out of Kanidm and remove the protected host's site cookies, or close all windows of the private session used for the test. Leaving the Kanidm session active can immediately sign the browser back in through SSO.

[#1293](https://github.com/kelchm/home-lab/issues/1293) tracks a compatible logout procedure, actual refresh/revocation timing and the required owner decision about central sign-out. No immediate-revocation guarantee has been established. See the upstream [validation and refresh logic](https://github.com/sevensolutions/traefik-oidc-auth/blob/v0.22.0/src/session.go#L104-L239) and [logout handler](https://github.com/sevensolutions/traefik-oidc-auth/blob/v0.22.0/src/main.go#L487-L539).

## Recovery

Recovery is normally automatic. If the pods remain in `CrashLoopBackOff`,
confirm that CoreDNS is ready and inspect the previous Traefik attempt:

```sh
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n network get pods -l app.kubernetes.io/instance=traefik-admin-network
kubectl -n network logs -l app.kubernetes.io/instance=traefik-admin-network \
  --previous --prefix --tail=100
```

After correcting DNS, egress, catalog reachability, or the pinned plugin
version, allow the kubelet retry to run. If its exponential backoff would delay
recovery, restart only the admin Traefik Deployment to create fresh pods:

```sh
kubectl -n network rollout restart deployment/traefik-admin
kubectl -n network rollout status deployment/traefik-admin --timeout=5m
```

Verify that each new pod logged `Plugins loaded.` and that a protected route no
longer returns Traefik's plain `404`. An unauthenticated command-line request
normally receives the OIDC middleware's `401` challenge:

```sh
kubectl -n network logs -l app.kubernetes.io/instance=traefik-admin-network \
  --all-containers --prefix --since=5m | rg 'Plugins loaded'
curl -o /dev/null -sS -w '%{http_code}\n' https://qbittorrent.home.kelch.io/
```
