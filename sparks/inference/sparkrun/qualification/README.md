# GLM successor qualification

This stdlib-only harness runs a bounded representative four-agent text/tool task against an explicitly supplied OpenAI-compatible endpoint. It records evidence locally and validates or compares it offline. Importing the package, exporting fixtures, validating, and comparing never contact an endpoint. It does not manage servers, connect to the Sparks, clear caches, restart anything, or run capacity/media/durability probes.

From `sparks/inference/sparkrun`:

```sh
python3 -m qualification fixtures --output /tmp/glm-fixtures --seed 20261106
python3 -m qualification run \
  --endpoint http://EXPLICIT-HOST:PORT/v1 --model GLM-5.3-Flash \
  --arm baseline --pair-id experiment-01 --output /tmp/glm-baseline.jsonl \
  --profile path/to/baseline.yaml --source path/to/baseline-source-lock.json \
  --cache-condition cold_prefill --request-deadline 60 --max-wall-regression 0
# Run the candidate separately, using --arm candidate and a fresh output path.
python3 -m qualification validate /tmp/glm-baseline.jsonl
python3 -m qualification validate /tmp/glm-baseline.jsonl --evidence /tmp/baseline-observations.jsonl
python3 -m qualification compare /tmp/glm-baseline.jsonl /tmp/glm-candidate.jsonl \
  --baseline-evidence /tmp/baseline-observations.jsonl \
  --candidate-evidence /tmp/candidate-observations.jsonl
```

`--profile` hashes the exact file bytes. Repeat `--source` for source/lock artifacts; the run records each basename and SHA-256 and a canonical aggregate hash. These identify the supplied artifacts; they do not establish which code a remote server actually runs. `--image-config sha256:...` records the expected image identity; a separate `runtime` observation must establish both running ranks and the observed source/image identity. Omission records JSON `null` (unknown). Fixture and harness source hashes are always computed. `--api-key-env NAME` reads a credential from the environment without recording it. Endpoint URLs cannot contain credentials or query strings. Output files are created exclusively and never overwritten; keep reports outside git. Reports contain synthetic histories and model reasoning.

Exit codes are **0 pass**, **1 fail**, **2 unknown**. A pass covers only this workload and the normalized observation checks described below; `adoption_status` stays unknown. Production adoption also requires the repeated campaign, quality, latency-tail, memory and recovery evidence in [#1280](https://github.com/kelchm/home-lab/issues/1280). A successful bounded run normally exits **2**: `workload_status` can pass while full qualification remains unknown. A correct short answer cannot establish the hard contract. A failed run retains partial wire data and errors, stops further turns in the affected session, and never retries.

## Workload and performance policy

Four sessions start together. Each has its own seeded ledger, marker, factors, and answer. Each session makes exactly three streamed requests: reason and call `multiply`, receive the locally checked tool result and answer, then recall the marker/result on a follow-up turn. The client replays every prior assistant message, including exact reasoning bytes and original server call IDs. Correctness requires exact tool arguments and exact final answers in the right session. This exercises client replay; it does **not** prove server restoration when clients omit reasoning.

Every request uses thinking, temperature zero, `max_tokens=1024`, and a finite total deadline (default 60 s, maximum 300 s). No request asks for a near-1M context or a 32K output. The runner sends no images/videos. HTTP responses are capped at 4 MiB each. DNS/connect, response headers, partial SSE lines, trickled heartbeats, and incomplete HTTP chunks all fall under the total deadline. On deadline, the caller returns, shuts down any established socket, and records failure; a daemon waiting in OS DNS can finish later and checks cancellation before sending. No automatic retries hide failures.

The primary metric is **end-to-end completion wall time for all four tool tasks**, measured from the first request start through the last validated response. It includes queueing, prefill, thinking, decode, local tool execution, and all continuation turns. Four initial requests must overlap. The default predeclared tolerance permits **no additional wall time**, with **four completed tasks and zero correctness failures**. A different tolerance must be chosen explicitly before the experiment; it is not an improvement threshold. Override the tolerance before either run; matched runs must declare identical policies. Changing it on one side invalidates comparison.

Pairs must match pair ID, fixture hash, workload version, harness hash, request deadline, contract, tolerance, and declared/observed starting cache condition. Profile/source/model and request seeds may differ across engines. Equal seeds do not imply equal generated text or token sequences. Thinking and usage totals are recorded; neither equal output token counts nor token-for-token equality is a cross-engine acceptance criterion. Wall ratios are descriptive until cache-condition evidence is verified. A single paired run provides no statistical confidence interval. `performance_status` checks the declared non-regression limit; the overall comparison and `improvement_status` remain unknown even for a faster pair. Repeat matched pairs in a counterbalanced campaign before an adoption decision and inspect tail behavior separately.

## JSONL evidence

All records carry `schema_version: "glm-qualification/v1"`, `kind`, and the same `run_id`. The first record is `run`; one `run_end` must follow all workload request/cache records. Requests retain the canonical body hash, full bounded request body, base64 SSE bytes and hash, HTTP result, total deadline/timing, parsed receipt, `server_usage`, and `server_cached_tokens`. Null means unknown. The validator reparses SSE, reconstructs the seeded task and exact history, checks hashes and timing, and recomputes correctness and metrics. Recorded `status`/`correct` claims cannot override these checks.

Passing requests require affirmative `[DONE]`, an appropriate `stop`/`tool_calls` finish, positive integer server prompt/completion usage, consistent totals if supplied, valid cached counts if supplied, and correct content/tool semantics. Missing usage fails a request; missing cached-token accounting stays unknown. Length stops, transport errors, duplicate JSON keys, conflicting receipts, malformed streams, missing turns, call-ID collisions, or changed reasoning histories cannot pass. Each request has a `cache` receipt with `mode: "unclassified"`; cached counts alone never establish cold prefill or durable restoration.

The covered contract checks additionally need externally collected observations. Supply these as JSONL using repeated `--evidence` (or the corresponding compare flags). Every observation must bind to that run's `profile_sha256` and `source_sha256`, and include retained artifact provenance:

```json
{
  "schema_version": "glm-qualification/v1",
  "kind": "capacity",
  "run_id": "COPY_FROM_RUN",
  "profile_sha256": "COPY_FROM_RUN",
  "source_sha256": "COPY_FROM_RUN",
  "provenance": {
    "artifact_path": "capacity-instrumentation.log",
    "artifact_sha256": "SHA256_OF_RETAINED_LOG_BYTES",
    "observer": "observer/tool-and-version"
  },
  "values": {
    "aggregate_live_pool_tokens": 2582528,
    "observed_total_window_tokens": 1048576,
    "observed_output_tokens": 32768,
    "concurrent_streams": 4,
    "correct": true
  }
}
```

Replace the placeholders and values with actual observations. Relative artifact paths resolve beside their JSONL file. Missing/unreadable artifacts or mismatched artifact hashes remain unknown. The validator checks normalized values and artifact hashes; it does not interpret arbitrary engine logs or independently authenticate operator attestations. Review the artifacts and their connection to the actual engine before accepting a normalized observation. Do not synthesize positive observations from configuration or a correct answer.

Exactly one receipt is required for each kind below, except `durability`, which requires one per listed event. All required values must be affirmative; missing receipts remain unknown and contradictory/malformed observations fail.

| Kind | Required `values` and meaning |
| --- | --- |
| `contract` | Exact configured values from `fixtures.CONTRACT`, excluding the two `*_floor` fields: `streams=4`, `total_window_tokens=1048576` **including** `output_budget_tokens=32768`, `image_tokens=2048`, `max_images=50`, `request_image_tokens=102400`, `max_videos=4`, `video_tokens=16384`, `video_frames=128`, `request_video_tokens=32768`; `durable_gib_per_rank` contains two configured values, each at least 64. Configuration is distinct from observed capacity. |
| `capacity` | Observed `aggregate_live_pool_tokens>=2582528`, `observed_total_window_tokens>=1048576` (input plus output), `observed_output_tokens>=32768`, `concurrent_streams=4`, `correct=true`. These expensive observations are collected separately; this runner never generates them. |
| `media` | Top-level `fixture_sha256` equals the run fixture hash. `image_sha256` lists all 50 fixture hashes in order; `per_image_token_cap=2048`; `per_image_tokens` contains 50 positive integer grid sizes at most 2048 (the cap is not the actual grid size). `history_tokens` and `history_grid_sha256` map each checkpoint `"1"`, `"2"`, `"3"`, `"49"`, `"50"` to its ordered token counts and fitted-grid identity hashes; every earlier prefix must stay identical as images are appended; `request_image_tokens=102400`, `images_correct=true`, `video_clips=4`, `videos_correct=true`, and `video_fixture_sha256` identifies the separately retained clip fixture manifest. Observe stable per-image allocation across the entire history, rather than accepting a shrinking budget. |
| `runtime` | `observed_image_config_id` matches the run's expected `image_config_id`; `observed_source_sha256` matches its source identity; `instance_ids` identifies both running ranks. Retain instrumentation that ties these observations to the service used by the workload. |
| `reasoning` | `original_call_ids` contains at least two distinct server IDs; `restored_call_ids` matches exactly; `original_reasoning_sha256` and `restored_reasoning_sha256` match; `omitted_reasoning=true`, `renumbered_id_cache_hit=false`, `cross_session_cache_hit=false`, `diagnostics_observed=true`. Use interleaved same-history calls and engine diagnostics/source-backed instrumentation. Correct answers alone do not prove exact-ID restoration or isolation. |
| `cache_state` | `condition` matches the predeclared run `cache_condition`, either `cold_prefill` or `warm_prefix`; `request_ids` lists the four first-turn IDs in session order; `cached_tokens` matches their server counts (all zero for cold, all positive for warm). Back the classification with instrumentation. The default declaration is unknown. Cold prefill never satisfies a durability gate. |
| `durability` | Top-level `event` is `eviction_restore`, `restart_restore`, or `crash_restore`. For each, record `storage="nvme"`, `ranks=[0,1]`, nonempty `before_instance_id`/`after_instance_id` (different after restart/crash), `prefix_sha256`, matching `before_cache_fingerprint_sha256`/`after_cache_fingerprint_sha256`, positive integer `restored_tokens` and `disk_read_bytes`, and `correct=true`. Retained instrumentation must establish real disk restoration on both ranks rather than fresh prefill, host-cache reuse, or an incompatible cache. |

The fixtures exporter writes a canonical manifest with per-session text/tool input/result hashes, the tool schema hash, and fifty deterministic color-swatch PNGs with unique seeded binary footers, SHA-256 hashes, and expected dominant colors. Their 2048-token per-image caps are requirements, not measured tokenizer/image-processor output. These small swatches exercise ordering and basic identity; they do not establish production screenshot detail or OCR quality. Payload token counts remain explicitly unknown without a tokenizer; server usage is authoritative for requests. Video assets, full window/output probes, eviction/restart/crash procedures, compatibility/invalidation tests, device/host memory telemetry, and real pi/Hermes acceptance remain separate work. The deterministic workload checks representative mechanics rather than broad coding quality.

## Local verification

```sh
python3 -m unittest discover -s tests -p test_glm_qualification.py -v
# Or use the existing test environment with requirements-test.txt installed:
python3 -m pytest -q tests/test_glm_qualification.py tests/test_long_sessions.py
```

The tests use only disposable loopback HTTP servers and temporary files. They cover successful multi-turn execution, deadlines, fragmented SSE/HTTP chunks, malformed/error receipts, incorrect answers, fixture hashes, missing evidence, capacity/media floors, exact-ID negative controls, cold-versus-durable distinction, and comparison tolerances.
