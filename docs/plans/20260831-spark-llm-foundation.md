# Spark LLM foundation: programmatic management and model swapping

**Status:** Active — 2026-08-31; the foundation (transaction + profiles + controller API + admission-controlled Caddy) is implemented in the change that carries this document, replacing PRs #440 and #462 outright. Extends [20260828-spark-control-plane.md](20260828-spark-control-plane.md): the switch transaction, profile model, and mutual-exclusion policy carry over unchanged; its "no daemon, no API" stance and always-routing static Caddy are superseded here.

## Where the prior workstreams stood

**Control plane (#440, draft).** The Ansible switch transaction — lock, teardown-both, MemAvailable reclaim gate, offline preflight with cross-rank image-ID equality, ready gate, coherence smoke, residency record — was designed and syntax-validated but never rolled out. Its static Caddy ("both ports, first healthy") had an identified admission bug: it could route to a candidate before the control plane accepted it, and chose ports autonomously rather than publishing the operator's decision.

**Model serving (#462, stacked).** The GLM-5.3-Flash EXL3+DFlash2 profile was validated live on the pair on 2026-08-30: full guarded switch, 1M context, cold boot to healthy in ~10 minutes, benchmarked. The validated profile existed only in the unmerged stack.

**Model consumption (2026-08-30 memo).** A source-verified review of Hermes and OpenCode concluded: no general-purpose LLM gateway (LiteLLM/Bifrost/Envoy AI/Kong all add mutable routing state and retry semantics a single mutually-exclusive backend must not have). Instead: Caddy as a switch-controlled admission layer, a `spark-resident` served-name alias, and launch-time real-ID selection for OpenCode.

**Execution substrate (2026-08-30 review).** `sparkrun` is the strongest reusable Spark runtime but has advisory locks and open teardown-correctness issues; parked as a candidate *driver* behind our own transaction, gated on a conformance spike.

## Direction

Keep the validated switch transaction and pinned-profile model as the ground truth of *how* a swap happens. Change *who can invoke it and from where*: the transaction is invocable both from an operator checkout (`task sparks:*`) and through a small resident controller on spark-1 exposing an authenticated HTTP API. One stable hostname fronts both planes — `spark.home.kelch.io/v1/*` proxies the committed backend through admission-controlled Caddy, `spark.home.kelch.io/admin/*` is the controller. Ansible converges host state and deploys the management plane (roles), and remains the transaction engine the controller executes (playbooks). No general-purpose gateway, no reconciler, no request-driven auto-swap.

What "programmatic" buys concretely: a swap is one authenticated API call (or one task) from any LAN machine — no repo checkout, no mise, no SSH keys on the caller. Agents and Hermes can query `GET /admin/v1/state` and request a switch as a tool call. Residency and switch history are queryable and journaled instead of living in the laptop's last SSH session.

## Job split

| Job | Owner |
|---|---|
| What *can* run: profile definitions, pins (guide rev, image digest, weight revs, served model ID), default profile, smoke contracts | Git — `sparks/ansible/profiles/`, merged to `main` |
| What runs *now*: teardown, start, verification, Caddy publication, residency | The transaction playbooks (`switch.yaml`, `publish.yaml`, `down.yaml`) — sole mutation authority, whichever path invokes them |
| Who may ask: operator via `task sparks:*`; agents via the controller API — never an inference request, health check, or file watcher | Laptop ansible + `sparks/controller/` on spark-1, serialized by the same lock on spark-1 |
| Host state: sudoers, boot convergence, driver assertion, management-plane deploy | Ansible roles (`host_baseline`, `caddy`, `controller`), run from the operator checkout |
| Client contract: stable URL, admission (maintenance 503 until a profile is committed), truthful `/v1/models` | Caddy on spark-1, published only by the transaction |
| Break-glass: dependency-free teardown, direct playbook invocation, direct ports `:8000`/`:8888` | `task sparks:down` (unchanged), laptop Ansible path, direct ports |

## What the foundation change contains

- `sparks/ansible/` restructured: **roles** converge state (`host_baseline`, `caddy`, `controller`); **playbooks** execute transactions. The validated switch core is carried over unchanged; new around it: maintenance-before-teardown, served-model identity verification on the direct port, exact-port publication only after the smoke, stable-endpoint verification, and a `publish.yaml` recovery path that never tears down. A failed switch leaves the endpoint in maintenance, never routing at whatever answered.
- `sparks/controller/`: FastAPI service, arm64 image built in-repo (`ghcr.io/kelchm/spark-controller`, `latest` + immutable `sha-<commit>` tags), deployed by the baseline. It executes the playbooks from its own checkout of `main` (synced only while no job runs), journals jobs to disk, refuses concurrent jobs, and holds no state a switch cannot rebuild. Bearer token in `sparks/ansible/secrets.sops.yaml`; the controller itself never needs SOPS.
- Boot convergence made route-complete: after a reboot, `spark-baseline.service` starts qwen *and* resets the stable route to it, so a route published for a TP=2 profile never outlives its containers. TP=2 still never auto-starts.
- All three profiles (qwen, deepseek, glm) with the pins validated live on 2026-08-30.

## Rollout

Each step is verifiable and abortable; nothing before step 4 touches the resident model.

1. Merge; wait for the `Spark Controller Image` workflow to publish `ghcr.io/kelchm/spark-controller:latest`. On the operator machine: `mise install` (ansible-core).
2. Create the `spark.home.kelch.io → 10.32.21.31` UniFi static record.
3. `task sparks:baseline` from the primary checkout (needs `age.key` for the token). With a TP=2 profile resident this deliberately fails closed: Caddy comes up routing `/v1` at the default profile's dead port. Verify `GET http://spark.home.kelch.io/admin/v1/state` (auth) shows the resident profile and healthy direct port, then `task sparks:publish PROFILE=<resident>` and verify `curl http://spark.home.kelch.io/v1/models` names it.
4. First API-driven switch in a maintenance window: `POST /admin/v1/switches {"profile":"qwen"}`, follow the job log; then a TP=2 profile end-to-end including its smoke. Re-verify `task sparks:down` from a bare checkout afterwards.
5. Update the opencode provider `baseURL` to `http://spark.home.kelch.io/v1` (per-machine, one time).

## Remaining phases

### Client model-identity contract

Per the memo, gated on a PoC that each pinned serving build accepts one `--served-model-name` with two tokens and answers with the real ID: add `spark-resident` as a served alias per profile (`/v1/models` stays truthful — the alias is never the only advertised name); migrate personal Hermes (fail-closed, local-only) and Flatrate (keeps its Luna fallback and pinned vision path) in separate surgical PRs; add an OpenCode launch helper that reads `/v1/models` once and starts with launch-scoped `model`/`small_model` overrides. If any build cannot satisfy the dual-name contract, the fallback is a purpose-built thin proxy — not a general gateway. Exit: switching profiles requires zero client config edits.

### Host IaC

Extend `host_baseline` (or sibling roles) to own the bring-up runbook's "good first automation targets": NetworkManager profiles from per-host vars, `/etc/hosts` fabric names, the nftables fabric guard and its systemd unit, pinned NCCL builds, and acceptance scripts emitting dated artifacts. Each conversion is validated live one host at a time; switch-port and VLAN/firewall changes stay manual per the runbook's boundary. Exit: a re-imaged Spark is physical steps + `ansible-playbook`.

### Observability + drift

Scrape controller, Caddy, and engine metrics from k8s-prod (closes the "Sparks are not continuously scraped" gap; coordinates with the estate observability plan in #429); switch jobs emit Grafana annotations. Add `task sparks:drift`: report upstream guide/image/weight movement against each profile's pins (the Mia guide moved 22 runtime-meaningful commits past the live pins in three days; detecting that today is manual research). Report-only — bumps stay deliberate PRs.

## Deferred, with named triggers

- **sparkrun as an execution driver** behind the same transaction: run the conformance spike (render-diff against validated guides, offline launch, no-restart-policy, worker-first parity, independent teardown verification) when a new profile would otherwise require hand-porting a guide, or when sparkrun ships the stop/cleanup fixes for its issues #277/#223.
- **Packaged gateway (Bifrost first)**: only if requirements emerge that are not Spark-residency — multi-backend routing across machines, per-client keys/quotas, centralized usage accounting. Slot: `clients → gateway → Caddy → committed backend`, lifecycle-unaware.
- **k8s membership, Nomad/Swarm, doco-cd, Komodo, request-driven auto-swap**: rejected in the 2026-08-28 record; nothing here reopens them.

## Open risks

- The controller adds a daemon to spark-1. Containment: it holds no unique state a switch cannot rebuild, every one of its actions remains executable from the laptop path, and it binds loopback with Caddy as the only front door.
- Playbook bugs become API bugs. The rollout gates (read-only verification first, first switches in windows) and the shared lock bound the blast radius; the job journal preserves the full playbook log.
- The controller executes profiles merged to `main` without a human at spark-1's keyboard. Merge review *is* the approval; profile edits go through branches per the repo's branch-vs-main rule.
- The deepseek `served_model_id` pin (`deepseek-v4-flash-dspark`) is taken from the live opencode provider config rather than a fresh boot of that profile; the first deepseek switch verifies it and the gate's failure message reports the actual ID if it differs.
- LAN-only bearer auth, plaintext HTTP. Acceptable on VLAN 21 today; if exposure widens (Tailscale, second cluster), TLS and per-client credentials become the trigger for revisiting.
- `host_baseline` grants kelchm NOPASSWD sudo on both hosts — non-interactive `become` for both invocation paths requires it, a sudo command allowlist cannot wrap ansible's become payloads, and docker-group membership is already root-equivalent on these hosts. Accepted for the loopback-fronted LAN plane; a dedicated control user is the revisit if credentials ever widen.
