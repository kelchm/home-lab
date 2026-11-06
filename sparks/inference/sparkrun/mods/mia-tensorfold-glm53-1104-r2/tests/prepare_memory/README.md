# Native GLM resident preparation contract — 090

`0900-native-prepare-memory-contract.patch` applies to the locked Mia108 source plus site020/025/030/040/050/060/065/070/071. It changes native code only. The supplied `HOME-LAB-SOURCES.json` identifies 5,265 input files; `source-manifest.json` records its checksum, the full build/source tree checksum, every changed file's before/after hash, the patch checksum, and fixture/runner checksums. The independent 080 checkpoint layer is absent from this receipt and remains integration work.

Patch SHA-256: `0426f2057219a4dbdbfc9558381688ead18e77f05d3b299ee9e456fcc969d736`.

The donor is `../../../bertholomus-tensorfold-dsv41-2970bcd-mia-fixes/patches/0007-server-prepare-memory.patch`, by Robert Wojciechowski, Apache-2.0. Its temporary-arena separation and `/proc/meminfo` parsing inform this implementation. The locked source already releases core tokenizer/template scratch. This layer releases additional prompt normalization, render traces, history IDs and probe results, measures the real allocators, and moves admission ahead of body allocation. No tokenizer arithmetic, image processing, media limits, context window, four-stream engine setting, native maxqueued setting, or launcher profile is changed.

Every POST and other request declaring a body enters the shared route boundary after authentication. With the optional policy enabled, a FIFO resident lease precedes body reading, JSON parsing, URL fetching, rendering, tokenization and `100 Continue`. Additional connections wait with their existing bounded headers and fixed connection buffers. A full waiting queue returns 429 with Retry-After; an admission deadline or missing headroom returns 503; cancellation removes the waiter; drain rejects waiters without reading their bodies. Existing per-key/rate admission still applies. Bodyless health/metrics requests remain available while the preparation queue is full.

A lease belongs to the request arena through engine completion, cancellation/draining of the native mailbox, response translation, and any warm-turn borrower. Warm passes retain their parent explicitly, have separately counted owned arenas, and participate in the existing drain count. They do not acquire another resident slot. Their encoded sources, deferred callback contexts, preparation-budget pointers, image/cache identities and TP borrows remain alive. Warm completion releases its own arena before its parent reference. The existing rule that remote URLs are not fetched a second time for speculative warm passes is preserved.

Temporary HfText rendering/tokenization and prompt probes use independently freed arenas. Only outputs and diagnostics are promoted. GLM's optional `Vision.prepare_scratch` callback records actual image-validation allocations while keeping encoded bytes and lazy contexts in the retained allocator. URL fetching copies the fetched encoded body into retained ownership before freeing temporary fetch allocations. Lazy materialization on an engine worker continues to use that engine's existing group/window allocator; 090 does not move those allocations into a request scratch arena.

The policy is disabled by default and no environment/profile is enabled by this package:

| Native environment variable | Default | Meaning |
| --- | ---: | --- |
| `TF_PREPARE_RESIDENTS` | `0` | Disable admission, or permit 1–128 resident HTTP/warm request families. |
| `TF_PREPARE_WAITING` | `32` | Up to 128 waiting connections, before body allocation; 32 matches the native candidate's engine queue allowance. |
| `TF_PREPARE_WAIT_MS` | `300000` | Admission wait deadline, 1–3600000 ms. Body/media deadlines begin separately. |
| `TF_PREPARE_FLOOR_MIB` | `0` | Optional Linux MemAvailable floor. Requires a positive reserve and resident count. |
| `TF_PREPARE_RESERVE_MIB` | `0` | Estimated additional headroom per resident family, including its warm pass; requires a positive floor. |

An enabled floor admits only when `MemAvailable >= floor + reservations already held + new reservation`. There is no first-request bypass. Missing/malformed telemetry waits and eventually returns 503. Reservations remain held for warm borrowers and deliberately conservatively charge already-resident requests again against current MemAvailable. They are estimates, not measurements or allocator byte limits. The slot cap is enforced independently of telemetry, including on macOS. Configuration parsing rejects invalid ranges and inconsistent floor/reserve combinations.

For owner-controlled hardware qualification, start with `TF_PREPARE_RESIDENTS=4`, `TF_PREPARE_WAITING=32`, and a wait deadline suited to observed queue latency. This permits four simultaneous engine consumers and moves additional preparation ahead of body reading. Evaluate eight resident families only after measuring the benefit of preparing ahead of four streams and its extra memory. Leave floor/reserve unset until measured peaks establish them; do not copy the DeepSeek donor's 3 GiB/24-bytes-per-text-byte assumptions into GLM. Keep the existing four engine streams and maxqueued32. These are proposed measurement settings, not deployment authorization or a claim that the largest admitted workload fits the target hardware.

`/metrics` exports `tensorfold_prepare_policy_*`, resident/waiting gauges and peaks, reservations, admission/wait/full/cancel/drain/timeout/missing-telemetry counters, and `tensorfold_prepare_memory_{live,peak,allocated,freed}_bytes{kind="…"}`. Policy `floor` and `reserve` values are bytes; `wait_ms` is milliseconds. The memory counters wrap production backing allocators, including successful alloc/resize/remap/free and failed-allocation cleanup:

| Category | Counted ownership and release |
| --- | --- |
| `retained` | Actual request and warm arena backing capacity: HTTP body, JSON/normalized values, promoted IDs/cache keys/media order, encoded source bytes, lazy contexts, preparation budgets, translation/reply data; released after the final borrower. |
| `render` | HfText JSON conversion, real template evaluation scratch and temporary rendered outputs; freed on return. |
| `tokenize` | Real tokenizer scratch fallback and temporary token/decode outputs; freed on return. The existing 4 KiB stack fallback is not heap capacity. |
| `prompt` | Normalization/render traces, history/probe IDs and temporary media-order structures; freed before submission. |
| `media` | GLM image validation decode scratch and native URL-fetch temporary allocations; freed after planning/fetch. |

These categories count allocator-requested backing capacity, including arena slack, rather than logical JSON bytes. They exclude connection buffers (139,264 bytes each), headers and their existing per-line/count bounds, thread stacks, kernel socket buffers, small request/control objects, the tokenizer memo, response/reasoning stores, library-internal mallocs, weights/KV, pinned/device/tower allocations and engine-worker media scratch. Categories' independent peaks are not a simultaneous RSS peak. Reservations must not be added to this ledger as if they were allocated bytes. Four leases bound concurrent resident request families, not absolute process RSS; a single large/expansive request still needs sufficient memory. The per-body limit is unchanged, and HTTP ArrayList/arena capacity can exceed logical body bytes.

Reproduce from the home-lab checkout:

```sh
python3 sparks/inference/sparkrun/mods/mia-tensorfold-glm53-1104-r2/tests/prepare_memory/run.py \
  --engine /tmp/glm1104-media-locked-source \
  --zig /Users/kelchm/.cache/zig-0.17.0-macos/zig \
  --cpu apple_m1
```

`--work-dir` may name a nonexistent directory. The runner verifies the entire locked input, copies it, checks/applies 090 without creating a Git repository, checks output hashes/formatting/lean differences, and executes only CPU tests and compilation. It checks the input again at completion. The negative control is enabled by default; `--skip-negative` explicitly omits it. The only fixture adaptation changes the earlier media test's stale per-clip expectation to match the already-locked 071 fair-share policy; production budgets are untouched.

The recorded candidate run passed **190/190 test executions, 35/35 build steps**. The unchanged baseline ran another 16/16 executions. The deliberate admission-bypass control failed the intended assertion (`waiting expected 8, actual 0`), with no compiler error. Lean findings remain inherited: see `receipt.json` for the exact baseline/candidate counts and zero-new-finding result. The tests use real sockets, native routing/body parsing/translation, production HfText/template/tokenizer and GLM PNG planning/materialization. The consuming engine is a delayed CPU recorder; no model or GPU is executed.

Coverage includes four delayed consumers plus eight successful queued arrivals; 32 waiting connections each declaring a 96 MiB body with zero additional request-arena allocation and no early `100 Continue`; FIFO order after cancellation; queue saturation, client disconnect, engine rejection/failure, malformed/chunked input, drain, injected low/missing/recovered telemetry, allocation failures in every real preparation phase, repeated image requests through streaming/whole OpenAI/Messages/Responses, counting/tokenize, and delayed warm callbacks after HTTP completion. Existing production GLM recording-driver tests exercise the shared media allocation, lazy video pairs, TP wire/ownership, failure cleanup and KV/protocol regressions. Native Linux CUDA open/vision-offer objects and CPU server binaries compile. `baseline.log`, `test.log`, `negative.log`, the apply/format logs and `receipt.json` retain the evidence; Zig may print `failed command` for successful tests emitting diagnostic stderr, so the runner checks exit status and the final summary.

The identical 32 KiB text prompt retained 799426 allocator bytes before 090 and 196818 afterward. The tool-image prompt retained 105680 before and 30322 afterward. Prompt IDs, cache-key SHA-256 values and image counts match exactly. These are prompt-arena receipts, not whole-request RSS; the HTTP test separately holds 382536 retained bytes during delayed consumption of its 32 KiB text request, releases every temporary category before consumption, and returns every category to zero after each repetition. Concurrency-dependent peaks are recorded in the logs rather than treated as fixed capacity constants.

Remaining qualification: 080 composition; full golden/broad host suites; real GLM tokenizer/checkpoint and maximum-window requests; actual four-stream throughput, queue latency and RSS/cgroup headroom on both ranks; JPEG/WebP/FFmpeg and live HTTPS allocator overhead; real video HTTP/warm passes; CUDA/Metal numerical behavior; actual TP failure/drain liveness; and pinned/device/tower peaks. Engine-worker media allocation coverage here comes from the existing CPU recording lifecycle tests, not a production hardware measurement. Frontend cancellation is cooperative; already-running render/codec calls retain their original completion/deadline behavior, and a disconnect hidden behind unread TCP data can remain queued until its bounded admission timeout. No process exhaustion, host change, branch, commit, push or publication was used.
