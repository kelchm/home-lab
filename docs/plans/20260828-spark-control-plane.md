# Spark inference control plane

**Status:** Active — 2026-08-30; the first control-plane slice is under review in #440, with the GLM profile following as a stacked change. Supersedes the Sparks-related deploy assumption in [20260620-nas-out-of-cluster-workloads.md](20260620-nas-out-of-cluster-workloads.md) (`sparks/README.md` previously pointed at doco-cd as the eventual deploy model).

## Problem

The Sparks swap 150–180 GiB inference stacks several times a week: a single-node vLLM profile, a TP=2 vLLM profile built from a pinned third-party guide with a locally-built patched image, and next a TP=2 SGLang profile. Profiles are mutually exclusive across the pair (both would claim the 121 GiB UMA pools; there is no watchdog; recovery is a physical power cycle), cold boots take 15–30+ minutes, and TP=2 requires worker-first launch ordering. The `task sparks:*` SSH path has no ready gate, no reclaim verification, no preflight, and the laptop's last SSH session is the only record of what runs.

## Decision

A small in-repo **Ansible control plane** (`sparks/ansible/`) over recipes kept in their **native upstream form**, with **docker restart policies + a boot-convergence systemd unit** owning boot-time state and a **static Caddy on spark-1** owning the stable client endpoint. No daemon, no reconciler, no new datastore.

The hybrid split — the piece the design hinges on:

| Job | Owner |
|---|---|
| What *can* run + what runs *by default* | Git: profile definitions in `sparks/ansible/profiles/`, `default_profile` in `group_vars`, pinned guide revs, digest-pinned images |
| What runs *right now* | `task sparks:switch PROFILE=…` — imperative, no commit per swap; residency recorded in `/opt/spark-stack/resident` + `switch.log` on spark-1, not in git |
| Reboot behavior | Caddy and the single-node default restart via docker policy / `spark-baseline.service`; **TP=2 profiles never auto-start** (unattended dual-rank bring-up is the documented UMA wedge path) |
| Recipe authorship | Upstream guides, pinned by git rev, cloned per host; this repo carries only overrides + a profile vars file. No porting into another tool's schema — a previous port attempt cost a working deployment (`sparks/inference/deepseek/README.md`) |
| Client contract | Caddy `:80` on spark-1 with `lb_policy first` + active health checks over `:8000`/`:8888`; mutual exclusivity means at most one healthy upstream, so the config is static. opencode's provider id stays `spark`; `baseURL` points at `spark.home.kelch.io` once; the models map carries a superset |
| Ready gate + exclusion + failure handling | The switch playbook: flock, teardown-everything-first, MemAvailable reclaim gate (refuses to start into unreleased UVM), drop_caches, preflight (guide rev, overrides, image present **and identical on both ranks**, weights present — no implicit pulls mid-rendezvous), 30-min health gate, cold-prefill smoke |

### What a switch does

`task sparks:switch PROFILE=deepseek` → lock on spark-1 → tear down every profile on both hosts → verify MemAvailable ≥ 100 GiB per host (else stop and instruct a reboot) → `drop_caches` → preflight → start (the guide's own script does the worker-first launch; single-node is a compose up) → poll `/health` up to 30 min → cold-prefill smoke (`replay_hermes.py COLD=1`; warm smoke tests pass on broken deployments) → record residency → unlock. On failure: diagnostics, then an instruction to run `task sparks:down`, which remains byte-identical and dependency-free as the break-glass path.

## Why not the alternatives

Research 2026-08-28, upstream issues and repos read directly; community evidence gathered from X. Full detail lives in the session transcript; the load-bearing facts:

- **doco-cd** (the NAS plan's engine): right for the NAS, wrong here — its own justification was *"set-once-and-forget, with essentially no drift to correct."* Cross-host ordering is open issue [kimdre/doco-cd#1617](https://github.com/kimdre/doco-cd/issues/1617); same-repo+ref deployments deploy in parallel; no mutual-exclusion concept; reconciliation state bugs #1735/#1746 open, plus a recurring redeploy-loop bug class — an accidental redeploy costs 15–30 min and risks UMA collapse.
- **Komodo** (the NAS plan's named promotion path — condition met, tool still fails): [#1120](https://github.com/moghtech/komodo/issues/1120) still open; no SOPS; lightest datastore is FerretDB+Postgres; ready gate and exclusion would be hand-written Deno Actions with no execution timeout (#1563); a hung periphery subprocess can wedge cross-host deploys silently (#1392).
- **k8s membership**: LeaderWorkerSet has no worker-first startup policy (only `LeaderReady`); the two public Spark-in-k8s deployments (nicolerenee/infra on Talos+DRA+dranet, route179 on EKS Hybrid) run *always-on single models* at monthly change cadence, not weekly swaps; GB10 page cache and GPU memory are one pool, which interacts with kubelet eviction; this repo's global HR force-remediation cannot be scoped off per-release. Zero community evidence of anyone swapping TP=2 Spark profiles under any general orchestrator (k8s, Nomad, Swarm, Komodo).
- **Nomad / Swarm**: neither has a cross-host startup-ordering primitive (Nomad lifecycle hooks are group-local; Swarm silently ignores `depends_on`) — a new raft daemon that still leaves the hard part hand-rolled.
- **lmswitch (jvr0x) — runner-up, revisit conditions named**: worker-first verified in code, local images fine, dual vLLM/SGLang runtimes, a DS4F-0731 dual recipe already exists in ai-models. Rejected for now: adopting means porting our proven stack into its YAML/runtime model onto a different image lineage, it has no host-level `drop_caches` hook, the serving hostname is a hardcoded constant, and its opencode sync destructively replaces the entire `provider` block (must stay disabled regardless). Flip if: the anemll-lineage recipe passes the Patch 3/4 + `replay_hermes` verification on this pair and `SPARK_HOST` becomes configurable. The profile contract here ports over trivially.
- **sparkduet / sparkstation / dgx-spark-router**: sparkduet independently converged on the same drain-first/doctor/gate design (good precedent; 3 days old, unlinked authors, model-per-git-branch layout); sparkstation is an always-on daemon platform; the router's own history disproves request-driven swap (production client-timeout incident from swapping inside the request path; single-node only).

## First slice (this change)

- `sparks/ansible/` — `switch.yaml`, `down.yaml`, `status.yaml`, `baseline.yaml`, inventory, `profiles/{qwen,deepseek}.yaml`, per-profile preflight/start task files, boot-convergence unit files.
- `sparks/caddy/` — digest-pinned Caddy compose + static Caddyfile.
- `.taskfiles/sparks/Taskfile.yaml` — `switch` and `baseline` wrappers; `deploy` removed; `down`/`status`/`logs` untouched.
- `.mise.toml` — `ansible-core` pinned via pipx backend.
- `spark.home.kelch.io → 10.32.21.31` added to the architecture doc DNS registry (UniFi static record to be created at rollout).
- opencode: one-time per-machine `baseURL` change to `http://spark.home.kelch.io/v1`; provider id stays `spark` (snippet in `sparks/README.md`).

Not in this slice: GLM-5.3-Flash and Qwen3.8-Flash-Next profiles (each lands later as pinned-guide + overrides + one profile vars file), SOPS wiring (no secrets exist in the Spark stack yet), metrics scraping of Caddy/vLLM from k8s-prod.

## Rollout

1. Merge; on the laptop `mise install` (ansible-core).
2. Create the `spark.home.kelch.io` UniFi static record.
3. `task sparks:baseline` — deploys Caddy and the boot unit to spark-1; verify `curl http://spark.home.kelch.io/v1/models` returns the resident model.
4. First guarded swap in a maintenance window: `task sparks:switch PROFILE=qwen` (cheap profile first), verify through Caddy; then `PROFILE=deepseek` end-to-end including the cold-prefill smoke.
5. Update the opencode provider `baseURL` on the laptop.
6. Rollback at any point: `task sparks:down` (unchanged), direct ports `:8000`/`:8888` still work if Caddy is removed.

## Open risks

- The deepseek start script is launched async (with `SKIP_OVERLAY_CHECK=1` — preflight owns image validity, and the script's checksum-triggered rebuild would outlive the ready gate); on gate failure the rescue path kills the lingering script. A script that "succeeds" while serving the wrong thing is caught by the cold-prefill smoke, not the gate.
- If spark-1 reboots while spark-2 still holds a TP=2 worker remnant, boot convergence starts qwen alongside it. Neither host is over-committed (qwen on spark-1, remnant on spark-2), it is visible in `status`, and `down` clears it — accepted rather than giving the boot path SSH access to the peer.
- The reclaim gate detects unreleased UVM but cannot fix it; the runbook answer stays "power cycle", and the playbook must keep refusing rather than retrying.
- systemd `Restart=` on single-node containers is delegated to docker's `unless-stopped`; a crash-looping vLLM under memory pressure is visible in `status` but nothing backs off automatically.
- Ansible runs from the invoking checkout; a switch from a dirty tree diverges live state from git silently. Assert-clean-tree is a candidate follow-up if it bites.
- Caddy health checks probe `/health` every 5s; an engine that serves `/health` before the model is actually usable would be routed to early — vLLM's `/health` only goes 200 after startup completes, so this is theoretical for current profiles.
