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
