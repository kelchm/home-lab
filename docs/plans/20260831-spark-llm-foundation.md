# Spark LLM foundation: programmatic management and model swapping

**Status:** Proposed — 2026-08-31; sequences the landing of #440/#462 and replaces the laptop-bound switch path with a resident control-plane API. Extends the [2026-08-28 control-plane decision record](https://github.com/kelchm/home-lab/blob/5d1d29937616e0fbf68d294527b67d05197935b5/docs/plans/20260828-spark-control-plane.md) (in #440, lands with Phase 1): the switch transaction, profile model, and mutual-exclusion policy carry over unchanged; its "no daemon, no API" stance and the always-routing static Caddy design are superseded here.

## Where the three workstreams stand

**Control plane (#440, draft).** The Ansible switch transaction — lock, teardown-both, MemAvailable reclaim gate, offline preflight with cross-rank image-ID equality, ready gate, coherence smoke, residency record — is designed, syntax-validated, and encoded in `sparks/ansible/`. It has never rolled out. The PR also carries a static Caddy ("both ports, first healthy") that a later review identified as an admission bug: it can route to a candidate before the control plane has accepted it, and it chooses ports autonomously rather than publishing the operator's decision.

**Model serving (#462, stacked on #440).** The GLM-5.3-Flash EXL3+DFlash2 profile was validated live on the pair on 2026-08-30: full guarded switch, 1M context, cold boot to healthy in ~10 minutes, benchmarked. GLM has been resident and healthy since. The validated profile exists only in an unmerged stack, so git does not describe what is actually running.

**Model consumption (2026-08-30 memo, unimplemented).** A full source-verified review of Hermes and OpenCode concluded: no general-purpose LLM gateway (LiteLLM/Bifrost/Envoy AI/Kong all add mutable routing state and retry semantics this single-backend service must not have). Instead: Caddy as a switch-controlled admission layer, a `spark-resident` served-name alias for clients that don't key behavior on model family, and launch-time real-ID selection for OpenCode. None of it is in a PR.

**Execution substrate (2026-08-30 review, parked).** `sparkrun` (spark-arena) is the strongest reusable Spark runtime — recipe schema, multi-node launch, image/model distribution — but its locks are advisory, teardown has open correctness issues (#277, #223), and worker-first parity with our validated guides is unproven. Conclusion: our transaction envelope stays authoritative; sparkrun is a candidate *driver* behind it, gated on a conformance spike.

The common failure mode across all four: analysis outran landing. The foundation below is mostly already designed and partly already validated — it needs to be sequenced, slimmed, and merged.

## Direction

Keep the validated switch transaction and pinned-profile model as the ground truth of *how* a swap happens. Change *who can invoke it and from where*: promote the transaction from operator-run playbooks on a laptop to a small resident controller on spark-1 exposing an authenticated HTTP API. One stable hostname fronts both planes — `spark.home.kelch.io/v1/*` proxies the committed backend through admission-controlled Caddy, `spark.home.kelch.io/admin/*` is the controller. Ansible's job shifts to what it is actually good at: IaC for the hosts and deployment of the management plane, plus remaining the transaction executor *inside* the controller. No general-purpose gateway, no reconciler, no request-driven auto-swap.

What "programmatic" buys concretely: a swap is one authenticated API call (or `task sparks:switch`, now a thin client) from any LAN machine — no repo checkout, no mise, no SSH keys on the caller. Agents and Hermes can query `GET /admin/state` and request a switch as a tool call. Residency and switch history become queryable state instead of a file only the laptop's last session knew about.

## Job split

| Job | Owner |
|---|---|
| What *can* run: profile definitions, pins (guide rev, image digest, weight revs), default profile, smoke contracts | Git — `sparks/ansible/profiles/`, merged to `main` |
| What runs *now*: switch execution, residency journal, Caddy publication | Controller on spark-1, executing the in-repo playbooks from a checkout that follows `main` |
| Who may ask: operator, agents, Hermes tools — via bearer-token API; never an inference request, health check, or file watcher | Controller API |
| Host state: driver hold, docker config, nftables fabric isolation, boot units, controller + Caddy deployment | Ansible roles, run from the laptop (rare, converging) |
| Client contract: stable URL, `spark-resident` alias, truthful `/v1/models`, launch-time real-ID selection | Caddy + served-name config + OpenCode launch helper |
| Break-glass: dependency-free teardown, direct playbook invocation, direct ports `:8000`/`:8888` | `task sparks:down` (unchanged), laptop Ansible path (retained) |

## Phases

### Phase 1 — Land what is validated (unblocks everything)

1. Slim #440: drop `sparks/caddy/` and the switch-time stable-endpoint check (superseded by the admission design in Phase 2; the static Caddy never deployed, so nothing rolls back), keep `baseline.yaml` to the boot-convergence unit, address the open review comments, un-draft, merge.
2. Rebase #462 onto the slimmed base and merge. Git now matches the live pair: three profiles (qwen, deepseek, glm), GLM resident.
3. Roll out the merged baseline (`task sparks:baseline`) and re-verify `task sparks:down` from a bare checkout.

Exit: `main` describes reality; the stack stops accreting.

### Phase 2 — Controller + admission (the fresh core)

New `sparks/controller/`: a small containerized service (image `ghcr.io/kelchm/spark-controller`, digest-pinned, Renovate-tracked, built in this repo's CI) deployed to spark-1 by an Ansible role. It shells the existing playbooks via ansible-runner from a local checkout of `main`, refreshed on a timer — merge a profile, and it is switchable within minutes, Flux-style, without the controller itself being a reconciler.

API (LAN + SOPS-managed bearer token; queue depth 1, refuses while busy; the playbook flock stays as the inner guard):

- `GET /admin/state` — resident profile, health, pins, memory, last switch result
- `GET /admin/profiles` — from the git checkout
- `POST /admin/switches {"profile": "glm"}` — 202 + job id; progress and logs via `GET /admin/switches/<id>`
- `POST /admin/down` — the guarded teardown

Caddy returns as the admission layer per the memo: git-owned boot config (maintenance 503 + `/admin` route), per-profile exact-port configs, published atomically by the controller only after the coherence smoke passes, unpublished (maintenance) before any teardown. No retry policy on inference POSTs. After a Caddy restart with a TP=2 profile resident, the endpoint stays failed-closed until an explicit republish. `spark.home.kelch.io → 10.32.21.31` lands in the DNS registry here.

Rollout gates: deploy read-only first (`state`/`profiles`), then enable switching and run the first API-driven switch in a window — qwen, then glm with the cold smoke. The laptop playbook path is re-verified afterwards as break-glass.

Exit: the laptop is no longer the control plane; `task sparks:switch` calls the API.

### Phase 3 — Client contract (swaps stop breaking clients)

Per the memo, gated on a PoC that each pinned serving build accepts one `--served-model-name` with two tokens and answers with the real ID:

1. Dual served names (`<real-id> spark-resident`) added per profile; `/v1/models` stays truthful — the alias is never the only advertised name.
2. Hermes: personal stays fail-closed local-only on `spark-resident`; Flatrate keeps its Luna fallback and pinned vision path. Separate surgical PRs against the live configs.
3. OpenCode: a launch helper reads `/v1/models` once and starts with launch-scoped `model`/`small_model` overrides (`OPENCODE_CONFIG_CONTENT`); provider id stays `spark`.

If any build cannot satisfy the dual-name contract, the fallback is the memo's purpose-built thin proxy — not a general gateway.

Exit: switching qwen↔deepseek↔glm requires zero client config edits.

### Phase 4 — Host IaC (a Spark is rebuildable from git)

Restructure `sparks/ansible/` into roles when this lands (the earlier best-practices review's layout applies here, not to the already-validated playbooks): `host-baseline` (driver 580.x hold, docker daemon config, nftables fabric isolation, sysctls, `/opt` layout, node exporter), `controller`, `caddy`. The bringup runbook shrinks to physical/fabric steps and points at the roles for everything convergeable.

Exit: re-imaging a Spark is runbook-physical-steps + `ansible-playbook`, not archaeology.

### Phase 5 — Observability + drift

1. Scrape controller, Caddy, and engine metrics from k8s-prod (closes the "Sparks are not continuously scraped" gap; coordinates with the estate observability plan in #429). Resident model, health, throughput, and switch events land in Grafana — switch jobs emit annotations.
2. `task sparks:drift`: report upstream guide/image/weight movement against each profile's pins (the Mia guide moved 22 runtime-meaningful commits past our live pins in three days; today detecting that is manual research). Report-only — bumps stay deliberate PRs.

## Deferred, with named triggers

- **sparkrun as an execution driver** behind the same transaction: run the conformance spike (render-diff against validated guides, offline launch, no-restart-policy, worker-first parity, independent teardown verification) when a new profile would otherwise require hand-porting a guide, or when sparkrun ships the stop/cleanup fixes for #277/#223.
- **Packaged gateway (Bifrost first)**: only if requirements emerge that are not Spark-residency — multi-backend routing across machines, per-client keys/quotas, centralized usage accounting. Slot: `clients → gateway → Caddy → committed backend`, lifecycle-unaware.
- **k8s membership, Nomad/Swarm, doco-cd, Komodo, request-driven auto-swap**: rejected in the 2026-08-28 record; nothing here reopens them.

## Open risks

- The controller adds a daemon to spark-1. Containment: it holds no unique state a switch cannot rebuild (residency journal is derivable from the hosts), and every one of its actions remains executable from the laptop path.
- ansible-runner-behind-an-API means playbook bugs become API bugs. The gates (read-only first, first switches in windows) and the unchanged inner flock bound the blast radius.
- A controller following `main` executes merged profiles without a human at the keyboard of spark-1. Merge review *is* the approval; direct-to-main profile edits are out per the repo's branch-vs-main rule.
- Dual served names are unverified on the exact patched builds; Phase 3 does not start until the PoC passes on all three.
