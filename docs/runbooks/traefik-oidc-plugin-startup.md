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

The `traefik-admin` HelmRelease has a `wait-for-oidc-plugin` init container. It
retries a validated HTTPS download of the exact pinned plugin artifact before
Traefik may start. The plugin version is a shared YAML anchor, so Renovate
updates the init check and Traefik static configuration together.

The guard intentionally fails closed. During a full outage the admin gateway
remains unready instead of silently serving only its unprotected routes.

## Recovery

Confirm that CoreDNS is ready and the plugin download is reachable from a pod,
then restart only the admin Traefik Deployment:

```sh
kubectl -n kube-system get pods -l k8s-app=kube-dns
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
