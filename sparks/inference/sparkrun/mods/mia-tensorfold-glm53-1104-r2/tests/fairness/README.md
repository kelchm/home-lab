# Native GLM slicing: blocked donor evaluation

No `1000-native-glm-prefill-slices.patch` is exported. The donor does not satisfy cancellation/retry or the existing host protocol contract. The immutable nine-patch source, launcher, locks and default whole-chunk behavior are unchanged. This directory is a reproducible blocker receipt, not a completed slicing implementation.

The fundamental blocker is cancellation between KDA layers. `Forward.mixer` switches the completed layer's recurrent-state parity and dispatches `convShift` immediately for prompt rows. `Forward.commit` is not the transaction boundary for those mutations. The donor's `cancelSlice` frees the partial descriptor and calls `snapshots.interrupted`, but does not undo them. The actual snapshot guard then refuses the preceding prefix with `NotAtMark`, because `State.pending` still names the suspended chunk. A same-prefix retry selects the cancelled layer's output as its next input. Initializing the missing lower bound, clearing the partial descriptor, or resetting the whole sequence cannot by itself preserve the preceding committed prefix.

Safe cancellation requires a design for restoring all state overwritten since the chunk boundary, with allocation and failure agreement across ranks. Recurrent parity alone is insufficient: convolution windows are changed in place, and index-ring rebasing and cache writes also need an explicit boundary contract. That prerequisite remains unresolved here. Completing the chunk during cancellation would instead change the cancellation latency contract. No such tradeoff is silently selected, and checkpoint/runtime work owned by the parent is untouched.

## Reproduced evidence

The fixture calls actual `Engine.prefillChunkSlice`, `Forward.runRange`, KDA launchers, `Leader.chunkSliced`/`cancelSlice`, the lane cancellation vtable and snapshot guard. CUDA/NCCL driver callbacks record production launch arguments and protocol messages; they do not calculate logits or emulate a model. The KDA witness uses the unchanged 4096-row prompt geometry, two layers, and one recording rank.

- After one slice at position 4096, actual host parity changes from `[0, 0]` to `[1, 0]`, `pending` becomes 4096, and a convolution-shift launch is recorded. Cancellation leaves parity and `pending` unchanged; the interrupted-prefix callback receives `NotAtMark`.
- The original recurrent input address is `0x200000`; the cancelled layer writes `0x220000`; the retry passes `0x220000` as its recurrent input. These are recorded arguments of the actual KDA step launcher, not predicted model outputs. The donor's own `zig/tests/cuda/glm_slice_tests.zig:124` cancels and retries without another `begin`, so this is also a scenario its supplied device check intends to support.
- The first-slice `lo` variable is uninitialized in the supplied donor. Debug execution returns `BadRange`. The normalized probes change only that declaration to `lo = 0` so the cancellation defects can be reached; this is not an implementation candidate.
- Inserting `chunkpart` in the middle of `Kind` renumbers existing protocol commands. The existing prompt-host test fails with `Kind.block` expected 12, found 13, even though native-open compilation succeeds.
- Seven required safety assertions fail: cancellation restores the completed prefix; retry uses the original state; a constant 2× timing converges to 2; cancellation at cached position zero drops the partial; cold calibration takes one layer; infinite calibration is rejected; and failed calibration allocation followed by retry leaves no orphan scratch. The donor reaches scale 8 and leaks 24 scratch bytes in the allocation/retry case. The test measures and then frees that orphan.
- A forced partial-ID allocation failure occurs after a `chunkpart` header and ID payload were broadcast, with no recovery header or KDA dispatch. Zig's failed struct assignment also leaves the partial optional populated; the fixture clears that incomplete descriptor without reading it. This is evidence of unsafe admission ordering, not a claim to have reproduced a live peer hang.
- Cold `sliceHi` returns both layers despite a 0.1 ms budget and owns three timing events; dropping its partial leaves those events alive. A one-layer calibration design would also need separate timing intervals so intervening decode work is excluded, final-layer accumulation, and cancellation/retry cleanup.

The positive controls exercise actual whole-chunk gate cancellation and an explicit fresh-request `begin` reset. An external source mutation that resets the sequence in `cancelSlice` fails the prefix-position assertion (4096 becomes 0), so resetting is not mistaken for prefix rollback. A separate one-line convergent EWMA mutation turns only the scale test green. Both mutations are reverted, and neither is exported as production code.

## Results and reproduction

The retained [receipt](receipt.json), [source manifest](source-manifest.json), [recording fixture](cancel_probe.zig) and [runner](run.py) identify the inputs and commands. The runner rejects changed input hashes, compilation errors, crashes, missing intended failure signatures and unexpected test success. Each command has a 600-second process-group timeout. It creates disposable copies, compares the locked source before and after, and sanitizes local path prefixes in retained logs.

```sh
python3 tests/fairness/run.py \
  --source /tmp/glm1104-media-locked-source \
  --donor /tmp/glm1104-slicing-donor.patch \
  --zig "$HOME/.cache/zig-0.17.0-macos/zig"
```

Run from this mod directory. The runner's successful exit means that the documented donor failures and controls reproduced; it does not mean the feature passed. The named output receipt has `status: blocked-no-production-patch`.

The relevant existing baseline gates pass 50/50 tests and compile actual GLM native-open. The resolved donor compiles native-open and passes 49/50 of the same tests; its protocol ordinal test fails. With only `lo = 0`, six witness/control tests pass and all seven required safety tests fail. The parent-provided broad baseline was 4421 passes, 18 skips and 319 steps; that broader suite is not rerun or claimed as new evidence here.

## Provenance and limits

The input is TensorFold `f350cd3ea253a042e205746928271f9d8f71a82d` plus Mia kit `02dac3879852b8c5713318d3d64a5a791002e954` (108 patches) and exactly site patches 020, 025, 030, 040, 050, 060, 065, 070 and 071. The source inventory contains 5265 files. Its canonical tree SHA-256 is `dd3fb2bdac49cbdfb9a155d380d86023d10173d5cee76e2049d5642f755ae2c4`.

The donor is MiaAI Lab's [PR 11](https://github.com/MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104/pull/11), supplied as open at head `63b235561e0df644b59567523b5982840f60cfc3`. Its [0108 patch](https://github.com/MiaAI-Lab/GLM-5.3-Flash-DGX-Sparks-1104/blob/63b235561e0df644b59567523b5982840f60cfc3/patches/engine/0108-glm-cuda-a-prompt-chunk-slices-between-decode-rounds.patch) has SHA-256 `527b0b51de355cbc4886478073089b0fc7abf3d611e2d15c4500c050c1ca499c`. The manifest records the exact Git blob, metadata checksum, base-file hashes and resolved-donor hashes. The one rejected hunk is inserted before the existing media `cancelMedia` method without changing its contents. Upstream status has not been independently refreshed.

No GPU arithmetic, actual multi-rank synchronization, token/logit equality, cache/restore equality, index-ring rollback, DFlash2 interleaving, throughput, TTFT or device failure recovery is qualified. The existing source's unchanged arithmetic is not evidence that a new interleaving preserves it.

The donor also advances a foreign owner's partial without reconstructing that owner's stack-owned prompt-logprob sink; `drainPartial` omits owner lane completion. These paths, mark/error propagation and pair fallback remain implementation work, not covered by this receipt. Media slicing is not implemented: the donor's new path would bypass 070's preparation/announcement protocol and shared-window lifetime. Any later candidate must explicitly retain the existing whole-chunk media path or implement and qualify the complete media ownership protocol. Slicing must stay opt-in and disabled in the launcher until real TP exactness, cache/restore and performance qualification are complete.
