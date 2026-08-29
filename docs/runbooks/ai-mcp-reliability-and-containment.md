# AI/MCP reliability and containment

This runbook records the baseline behind issues #294 and #295, the allowed
dependency graph, and the post-deploy checks for the reliability and network
policy changes.

## 2026-07-31 baseline

Live state before the change:

- The MetaMCP pod had restarted 64 times in 38 days. Its latest termination was
  `OOMKilled` at the 1 GiB limit after an 8h40m container lifetime.
- That terminated container created 15 public Streamable HTTP sessions and
  cleaned up none. The replacement had 53 active sessions after 3d15h and used
  about 295 MiB when sampled.
- MetaMCP 2.4.22 contains a five-minute expired-session cleanup loop, but the
  `SESSION_LIFETIME` row was absent, which means infinite retention. Sessions
  were only removed when clients sent MCP `DELETE` requests.
- Each public MetaMCP session creates connections to the configured downstream
  servers. The broken Grafana connection returned `HTTP 403 forbidden: host not
  allowed` during every session setup.
- MetaMCP 2.4.22 is the latest upstream release. The 2.5 tags are an unreleased
  Docker-per-MCP architectural rewrite, so they are not used as a reliability
  fix.

The stable image has no log-level control: its per-session `console.log` calls
are unconditional. Revisit `LOG_LEVEL` when upstream publishes a stable release
that contains it instead of carrying a local image patch.

The initial rollout set **Settings → Session lifetime** to 240 minutes. It was
deliberately rolled back on 2026-08-03 because clients, particularly Claude,
continued using sessions after the server expired them instead of
re-initializing. Any nonzero lifetime eventually turned normal client traffic
into a user-visible failure, so `SESSION_LIFETIME` remains unset until client
behavior changes. The functional probe must not assert a session lifetime.

Follow the registry-hygiene procedure in `metamcp-bootstrap.md` to remove the
package-downloading `time` server. Keep the auto-generated
`<endpoint>-endpoint` MCP server rows; creating an Endpoint in the UI inserts
those so the in-process inspector can dial `APP_URL`. That path needs the
`traefik-admin` hairpin allow.

With expiry disabled, session growth remains unbounded. The 2 GiB memory limit
is headroom, not proof of containment, and must not be reduced merely because a
14-day window passes without an OOM. Track memory growth against session
activity and replace random OOM disruption with a deliberate containment
mechanism, such as a restart in a known quiet window, before lowering it.

## Allowed dependency matrix

| Source | Allowed destination | Ports | Reason |
| --- | --- | --- | --- |
| All AI workloads | CoreDNS pods | UDP/TCP 53 | Cluster DNS |
| MetaMCP | `metamcp-db` pods | TCP 5432 | PostgreSQL |
| MetaMCP | Nine exact MCP pod identities | Port owned by each backend's ingress policy | Tool aggregation |
| MetaMCP | Kubernetes Service `network/traefik-services` | Effective pod TCP 8443 | `auth.home.kelch.io` Kanidm OIDC |
| MetaMCP | Kubernetes Service `network/traefik-admin` | Effective pod TCP 8443 | `metamcp.home.kelch.io` APP_URL hairpin for the in-UI endpoint inspector |
| Grafana MCP | Grafana pods in `observability` | TCP 3000 | Read-only Grafana API |
| LEMON manuals MCP | `lemon-website` pods in `lemon-manuals` | TCP 8080 | Self-hosted manual page retrieval |
| Kubernetes/Flux MCP | Cilium `kube-apiserver` entity | API server ports | Read-only cluster APIs |
| Grafana functional probe | Grafana MCP pods | TCP 8000 | Initialize, tool call, and session cleanup |
| Browser, document, weather, and parts MCPs | Public IPv4 only | TCP 80/443 | Untrusted web/API fetches |
| Prometheus and vmagent | `metamcp-db` pods | TCP 9187 | CNPG metrics |
| CNPG operator | `metamcp-db` pods | TCP 5432 and 8000 | Reconciliation, failover, and recovery |
| Cluster nodes | `metamcp-db` pods | TCP 8000 | CNPG instance management |
| Same-cluster CNPG pods | `metamcp-db` pods | TCP 5432 and 8000 | Replication and future scale-out |
| `metamcp-db` pods | CoreDNS, same-cluster pods, and Cilium `kube-apiserver` entity | DNS, 5432/8000, API server | CNPG lifecycle |

The public-egress rule explicitly excludes pod/Service space, all RFC 1918
space, loopback, link-local, CGNAT, benchmarking, documentation, multicast, and
reserved IPv4 ranges. IPv6 is disabled in Cilium; equivalent exclusions must be
added before enabling it.

There is no hard-coded LAN CIDR. MetaMCP egress names the exact backend pod
identities once; each backend's reciprocal policy admits MetaMCP only on that
server's MCP port. Cilium requires both policies to allow a connection, keeping
identity and port ownership local without a central identity/port cross-product.
When adding a backend, add its identity to the central list and its exact port to
the backend ingress policy. The unused vanilla `playwright-mcp` workload was
removed; the checked-in and live MetaMCP inventories use only
`playwright-stealth-mcp`.

## Functional checks

The `grafana-mcp-functional-probe` CronJob runs every five minutes. It uses the
exact Grafana MCP URL MetaMCP stores, initializes MCP, calls `list_datasources`
(which must reach Grafana), verifies the response, and deletes the MCP session.
A TCP-only success cannot satisfy the check. Session-lifetime policy is
intentionally outside this probe.

After rollout:

```sh
kubectl -n ai wait --for=condition=available deployment/metamcp deployment/grafana-mcp --timeout=5m
kubectl -n ai create job --from=cronjob/grafana-mcp-functional-probe grafana-mcp-manual-probe
kubectl -n ai wait --for=condition=complete job/grafana-mcp-manual-probe --timeout=2m
kubectl -n ai logs job/grafana-mcp-manual-probe
```

The scheduled check intentionally has no MetaMCP API key. Immediately after
rollout, use an existing private API key to exercise the authenticated
aggregator path with the same probe implementation (the key remains in the
shell environment):

```sh
MCP_SCHEME=https \
MCP_HOST=metamcp.home.kelch.io \
MCP_PORT=443 \
MCP_PATH=/metamcp/default/mcp \
MCP_TOOL_NAME=grafana__list_datasources \
MCP_API_KEY="$METAMCP_API_KEY" \
python3 kubernetes/apps/ai/grafana-mcp/app/probe.py
```

This is the post-rollout acceptance check for MetaMCP database URL correctness,
MetaMCP egress policy, aggregator initialization, Grafana's Host allowlist, and
the Grafana API call. It also sends MCP `DELETE`, so the check does not add
retained sessions.

Expected final log:

```text
Grafana MCP functional probe passed via metamcp.home.kelch.io (5 datasources)
```

Confirm the APP_URL hairpin used by the in-UI inspector (must return JSON
`{"status":"ok"}` within five seconds; a hang is a policy miss):

```sh
kubectl -n ai exec deploy/metamcp -- node -e \
  "fetch('https://metamcp.home.kelch.io/health',{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log)"
```

Check session cleanup and memory without exposing session IDs:

```sh
kubectl -n ai exec deploy/metamcp -- node -e \
  "fetch('http://127.0.0.1:12009/metamcp/health/sessions').then(r=>r.json()).then(x=>console.log({sessions:x.streamableHttpSessions.count,total:x.totalActiveSessions}))"
kubectl -n ai top pod -l app.kubernetes.io/name=metamcp
kubectl -n ai logs deploy/metamcp | grep 'Cleaning up.*expired StreamableHTTP'
```

## Connectivity test matrix

Use a disposable pod carrying the same label as a public-fetch workload. A
successful connection to any negative target is a policy failure.

```sh
kubectl -n ai run ai-egress-test --rm -i --restart=Never \
  --image=docker.io/curlimages/curl:8.16.0 \
  --labels=app.kubernetes.io/name=markitdown-mcp -- \
  sh -eu -c '
    curl -fsSI --connect-timeout 5 https://example.com >/dev/null
    ! nc -z -w 3 10.43.0.1 443
    ! nc -z -w 3 10.32.30.8 6443
    ! nc -z -w 3 10.32.25.5 2049
  '
```

| Test | Expected |
| --- | --- |
| Public HTTPS (`example.com`) | Allowed |
| Unrelated ClusterIP (`10.43.0.1`) | Blocked |
| Node/management API VIP (`10.32.30.8`) | Blocked |
| Storage/NAS address (`10.32.25.5`) | Blocked |
| Scheduled Grafana MCP tool call | Allowed |
| MetaMCP to each declared MCP and PostgreSQL | Allowed |
| MetaMCP OIDC discovery and interactive login | Allowed through `traefik-services` |
| MetaMCP APP_URL hairpin (`https://metamcp.home.kelch.io/health` from the pod) | Allowed through `traefik-admin` |
| CNPG operator status/reconcile after instance restart | Healthy |
| Kubernetes/Flux MCP read calls | Allowed |

Remove a manual probe Job after reviewing it:

```sh
kubectl -n ai delete job grafana-mcp-manual-probe
```

## Alerts and memory monitoring

`ai-mcp-reliability` alerts on a new MetaMCP OOM kill, two restarts within 30 minutes, a failed/stale functional probe, or twelve minutes with no probe success metric. `MetaMCPOOMKilled`, `MetaMCPRepeatedRestarts`, `GrafanaMCPFunctionalProbeFailed`, and `GrafanaMCPFunctionalProbeMissing` route through the shared Alertmanager. Prometheus owns external delivery while the vmalert copy remains available for comparison in the UI; use the [alerting runbook](alerting.md) to test or troubleshoot the notification path.

For issue #294, keep the issue open until the authenticated MetaMCP probe above
passes. Record pod age, restart count, last termination reason, session count,
current memory, and memory growth per unit of session activity as diagnostic
data only. No reproducible closure threshold currently defines the session
unit, observation window, and acceptable growth rate, so these observations and
a 14-day no-OOM window must not be used to remove the 2 GiB limit or close the
reliability item. A replacement containment design must define and validate its
own acceptance criteria first.
