# Native checkpoint foundation gate

`../../patches/0500-native-checkpoint-foundations.patch` adds native primitives and a dedicated `zig build test-checkpoint-bounded-host` target. It applies to the supplied Mia108 engine with the upstream2103 pinned-download backport. `source-manifest.json` records the exact input and output hashes of every affected source file. The Python runner only copies, applies, verifies hashes, and invokes Zig; all ownership, capture, transfer, and filesystem behavior lives in Zig.

The patch exports `core.checkpoint_capture`, `core.checkpoint_stream`, `core.checkpoint_file`, `glm.checkpoint`, and `glm.checkpoint_freeze`. The core capture and stream tests run through the existing core module; the target also runs all existing `kv_pool.zig` tests, the rank writer tests, and the actual GLM State/Plan tests under a recording CUDA driver. The rank writer takes the existing rows file module as a comptime argument so the serving API retains its single ownership of that module.

## Apply and test without changing the source

From the home-lab repository root:

```sh
/tmp/glm1104-qualification-venv/bin/python \
  sparks/inference/sparkrun/mods/mia-tensorfold-glm53-1104-r2/tests/checkpoint/run.py \
  --source /tmp/glm1104-r2-engine \
  --zig /Users/kelchm/.cache/zig-0.17.0-macos/zig
```

The runner retains a new scratch directory and prints its path. It rejects a changed base, an altered patch, and an existing output directory. Use `--work-dir /tmp/new-checkpoint-build` to choose a new scratch path. There is no fetch, clone, commit, or device operation.

For an already composed engine tree containing prefix050, run directly in that tree:

```sh
/Users/kelchm/.cache/zig-0.17.0-macos/zig build test-checkpoint-bounded-host --summary all
```

For manual application, copy the prepared engine excluding its caches, then run `git apply --check /absolute/path/to/0500-native-checkpoint-foundations.patch` followed by `git apply` with the same argument in the copy. No Git repository initialization is needed. Registering prefix050 in the site's lock/build input remains the parent task's responsibility.

## Ownership and capture contract

- One `checkpoint_capture.Budget` owns admission for one actual `kv_pool.Pool`. Configure explicit descriptor, unique physical page, per-descriptor metadata, and total capsule-byte limits. The pool and budget outlive every descriptor. All accounting calls run on the pool's owner thread.
- Checkpoint pins are a distinct subset of actual pool references. They do not alter `Pool.promised`; they never count as evictable cache pages, cannot be released through the cache's `drop`, and survive a live lease finishing. Shared prefixes across forks consume the unique-page budget once. Pool destruction asserts that all checkpoint pins have drained.
- `Capture.create` requires a paged State at a committed mark, the exact live lease/table, and a freezer. Only complete physical pages through the minimum of committed target and included MTP lengths are pinned. A partial target/MTP page and pooled lookahead go into the capsule. A head lag exceeding one complete page is rejected as `UnsupportedHeadLag`, preventing an empty or stale head from turning the capsule into a copy of the target prefix. Nonpaged state, KDA state and parity, index rings and their bases, prompt hidden state, and any supplied DFlash2 ring are copied or owned in descriptor metadata. An absent freezer returns `UnsupportedMutableCapture`; unpaged capture and pending forwards are rejected explicitly.
- `glm.checkpoint_freeze.Device` is a concrete CUDA D2D freezer. It allocates only the admitted capsule, streams mutable Plan pieces into it, and fences before returning. Failed enqueues drain before freeing the destination. The caller must hold the sequence paused until it returns and order prior writers on the same stream. The integration must also prevent rewinds, resets, or forks from overwriting pinned completed pages; pool pinning alone is not write protection.
- `Cursor.source()` walks the actual `cuda_snapshot_layout.Plan.piece` geometry into immutable device or capsule spans. It does not build an array of chunks or admit a full-prefix snapshot to a RAM cache. Descriptors own their page IDs, KDA parity, positions, ring bases, and drafter context origin.
- `Ring.init` borrows the descriptor and partitions caller-owned storage into a fixed number of slots. Production CUDA storage must be pinned. Tagged completion keys reject stale reuse; out-of-order DMA completion is allowed. A slot remains occupied until DMA has completed or drained and a started write has completed. Cancellation skips only writes that never started. Closing a descriptor with a borrowed ring, or closing a ring with active work, returns `CheckpointBusy`.
- `checkpoint_file.Writer(existing_rows_file_module)` is a sequential rank-local sink. Out-of-order chunks return `CheckpointSinkBackpressure`. It uses `rows_file.Writer.openUnique`, the existing rows payload/trailer/hash format, and a SHA-256 rank/epoch domain over the supplied rows block identity. The order is payload and bounded metadata, file fsync, close, no-replace hard-link publication, temporary unlink, directory fsync. Only then can it return a local publication receipt. The `.rank` extension is not indexed by the serving store.

## Evidence and boundaries

The final packaged patch applies cleanly in a fresh scratch copy, passes `zig fmt --check`, and passes 77/77 host test executions across four binaries (10/10 build steps). `receipt.json` records the patch hash, counts, geometry, and baseline failures.

The native tests cover four growing leases with outstanding checkpoint pins, shared forks, metadata and capsule admission, every pool/descriptor allocation failure, delayed DMA, delayed I/O, cancellation, worker failures, stale completion keys, and a 12 GiB logical source with a fixed 2 MiB transfer ring. GLM tests use actual State construction and Plan walking for BF16 and FP8, reordered physical pages, page boundaries, partial target/MTP tails, live-state mutation after freezing, and native freezer failure cleanup.

At 1,000,001 tokens, the default TP2 FP8/no-target-MTP fixture has 6,649,248,044 payload bytes, a 269,656,364-byte mutable capsule including DFlash2's 41,943,040-byte ring, and 1,953 pinned page IDs occupying 7,812 bytes. The test asserts the capsule has the same size at 8,257 tokens with the same page offset. Device allocation/copy accounting uses a fake CUDA ABI; this is a boundedness and geometry receipt, not a measurement of GPU throughput or RSS with real device allocations. The small fixtures verify copied bytes through the actual CUDA ABI adapter.

The existing `test-glm-snapshots-host` suite was run on both the patched tree and an independent copy of the supplied base: both report 22 passes and the same four failures. Two compare 222,298,112 against 260,046,848 snapshot bytes; two expect a prompt-cache hit. These source-baseline failures were not changed. The broad `test-cuda-host` suite is not claimed green.

This is an unenabled foundation. Remaining integration includes the engine's capture pause and page-write/COW guards, pinned transfer-buffer admission and worker scheduling, TP agreement on capture/failure/cancellation, all-rank epoch manifests and crash recovery, checkpoint metadata serialization/validation, restore into new page tables, page/block reuse and garbage collection, storage quotas, and real CUDA/NVMe qualification. A rank file or local receipt must never be treated as an all-rank durable checkpoint. No server flag, storage capability, restore behavior, or automatic fallback is added by this patch.
