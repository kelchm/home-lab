# Native GLM checkpoint restore and epoch gate

`../../patches/0800-native-checkpoint-restore-epochs.patch` extends the **actual prefix050 implementation** in the supplied `/tmp/glm1104-r2-integrated` tree (Mia108 plus 020/030/040/050/060). It adds versioned native metadata, bounded verified restore into an admitted inactive live lease, and a filesystem coordinator that commits and recovers complete all-rank epochs. It does not enable server durability. The source input remains read-only; the patch and this directory are this implementation's only repository outputs.

## Apply and reproduce

From the home-lab repository root:

```sh
python3 sparks/inference/sparkrun/mods/mia-tensorfold-glm53-1104-r2/tests/checkpoint_restore/run.py \
  --source /tmp/glm1104-r2-integrated \
  --zig /Users/kelchm/.cache/zig-0.17.0-macos/zig
```

The runner checks the patch hash, changed-file input hashes and required primitive hashes; copies the source without `.git`, caches or build output; applies the patch; checks every resulting file hash; runs `zig fmt --check`; and executes `test-checkpoint-restore-host` and the existing `test-checkpoint-bounded-host`. It preserves its scratch directory and `checkpoint-host.log`. `--work-dir /tmp/new-checkpoint-restore-build` selects another new scratch path. No Git checkout/worktree, device operation, download, branch, commit, push or deployment is involved. Zig 0.17 is required. The new host target gives **every new test module explicit `apple_m1` code generation on arm64 macOS**, including its GLM/core/CUDA dependencies.

For root's separately composed tree, reconcile patch contexts normally and run the targets directly:

```sh
/Users/kelchm/.cache/zig-0.17.0-macos/zig build \
  test-checkpoint-restore-host test-checkpoint-bounded-host \
  -Dcpu=apple_m1 --summary all
```

The runner intentionally rejects a changed input; its hashes describe the supplied integrated tree, not later 025/065/070/071 composition. Export additions in `cuda.zig`/`core/root.zig` and the one build registration can need context reconciliation. No body, image preparation, video allocation, media lifecycle or server capability code is changed. `rows_file.zig` only exports its existing bounded range reader and replaces unchecked trailer-size addition with checked arithmetic, so a corrupted trailer cannot trap the reader.

## Native interfaces and ownership

`glm.checkpoint_restore.Metadata.fromCapture` reads an actual paused `checkpoint.Capture` and its matching State. Fixed little-endian records encode version/magic, FP8 versus BF16 and target-head policy, global and local geometry, mark and MTP length, KDA parity, both index-ring bases, a fingerprint of every actual Plan part, payload length, required destination page count, DFlash2 ring size/origin, and media identities/geometry. The payload follows the existing Plan, including FP8 codes **and scales**, both recurrent-state copies, convolution/projection state, sparse pooled lookahead, index rings, MTP caches and prompt hidden state. No state is converted or approximated.

The media record contains a processor digest and at most 64 canonical prepared-image digests with token position/count and grid height/width. Every image must be fully consumed by the checkpoint mark, have valid GLM patch geometry, and have an ordered nonoverlapping token span. Incomplete media is explicitly refused. Raw pixels, remaining feature-row buffers, decoder handles, preparation-budget pointers and other request-lifetime state are not serialized. Root's 071 ephemeral preparation-budget pointer therefore does not enter this format. Reconstructing media state for an unfinished span remains an integration task.

The common `Agreement` binds version, epoch, committed mark, rank count and SHA-256 identities for model, optional drafter, arithmetic policy, layout, media and token/request prefix. Model/arithmetic/layout/media/prefix digests must be present; an all-zero drafter digest means no external drafter and must agree with the native metadata. Use canonical model/drafter content identities, a digest covering the complete arithmetic/program policy, and a stable token/request identity for the epochs being recovered; paths and display names are insufficient. `Metadata.bind` derives the layout and media digests. The shared layout identity accommodates uneven TP head partitions, while each rank retains its exact local geometry; the envelope validates the rank's expected KDA head count and restore checks its real destination shape.

Instantiate `core.checkpoint_epoch.Store(existing_rows_file_module)` with the serving store's existing rows module, preserving that module's owner. Its required `validate_detail` callback is `glm.checkpoint_restore.validateEnvelope`; all rank files pass this family validation before commit or recovery. Encode `epoch.Envelope` followed by `Metadata` with `epoch.wire.encode`, and pass the two slices as metadata to the existing prefix050 rank writer's `seal`. Its payload still comes from `checkpoint.Cursor` through the existing bounded transfer ring. A rank writer's `Local` receipt is **never** a complete checkpoint.

For restore, allocate an inactive paged State with its normal arena/page-table capacity, admit an empty live lease with `Pool.promise`, and separately admit any DFlash2 destination ring and pinned transfer slab. `Inactive.init` refuses a nonempty/live State or lease. `Inactive.load` takes the rank reader returned by a verified epoch, validates the metadata, takes only the required pages through the actual `Pool.cover`, and maps the newly allocated IDs into the destination page table. It reads through the bounded slab and walks the actual `Plan.piece` destinations, including reordered physical pages. It never allocates a prompt-cache lease, a second complete state image or a payload-sized host buffer.

Every upload is fenced before the slab is reused. Both rows checksums and the committed SHA-256 rank digest must pass before host cursor/parity/base restoration can produce a local `Ready`. Any read, allocation, copy, cancellation or final cursor-upload failure poisons the destination and drains outstanding work. The caller must then `release` its lease and discard the inactive State/ring; partial host fields are not an activation signal. A successful owner retains its lease until the sequence ends. `Ready.ring_from` supplies the saved DFlash2 origin, but calling the live drafter's resume operation and binding the sequence remain scheduler work.

## Epoch publication and recovery

A coordinator accepts exactly one matching local receipt per rank, reopens its real file, validates its envelope/family metadata and streams the payload to verify it. Missing, duplicate, stale or mismatched ranks fail the epoch. Commit repeats verification and fsyncs every rank file, fsyncs the store directory, writes and fsyncs the manifest temporary file, closes it, publishes with a no-replace hard link, removes the temporary name, then fsyncs the directory again. Cancellation is checked during reads and before/after manifest publication. A returned epoch means this entire sequence succeeded.

Manifest names reserve one publication slot per binding/world/epoch, independently of the proposed mark; two coordinators cannot publish different marks under the same epoch. Rank names still bind the full agreement, including mark and rank. Published rank files and manifests are immutable. Failed publication removes only the new manifest link and attempts to sync that rollback; previous epochs are never overwritten or deleted. Failed attempts can leave orphan rank or temporary files, which recovery ignores. There is deliberately no garbage collection or implicit quota policy.

`Store.recover` scans only `.epoch` manifests, verifies the expected identity/world, validates every rank's metadata and every payload byte, and chooses the newest fully valid epoch. A missing/corrupt latest rank, manifest or trailer cannot hide an older valid epoch. It never promotes `.rank`, `.part`, local receipts or an in-memory “all done” counter into durability. The reader rechecks the selected rank during restore, catching changed/truncated bytes after initial recovery. Cancellation returns cancellation, not an older checkpoint presented as success.

The directory must already belong to the store and be durably provisioned, including its parent directory entry. One coordinator owns an epoch. The current filesystem coordinator needs a durable directory view containing every rank file; this is not a distributed filesystem or an implemented TP/NVMe transport. A future deployment must supply that view or an equivalent verified all-rank persistence protocol. Actual crash/power-loss durability still depends on the filesystem honoring fsync; an unacknowledged but complete publication can survive a crash and be discovered, while incomplete publications are rejected.

## Evidence and limits

`receipt.json`, `source-manifest.json` and `evidence/` record the final patch, inputs, commands and outputs. The new target executes 25 passing host tests across two binaries (12 with the actual GLM State/Plan and recording CUDA ABI, 13 for filesystem epochs and the wire format). The existing prefix050 target contributes 77 passing executions, for **102/102** across the two targets in a fresh patch application.

The native cases restore BF16/FP8 state at page boundaries and partial tails, map different physical page IDs, compare all Plan bytes, verify KDA parity/index bases/MTP cursors, and restore DFlash2's actual 40 MiB ring and `ring_from`. Failures cover destination device/pinned/lease allocation, partial CUDA copies, cancellation, corrupted/truncated bytes after recovery, rejected metadata and final cursor-upload failure. Filesystem cases cover absent ranks, stale envelopes, every common identity mismatch, duplicate ranks, a recomputed rows checksum that still fails the manifest SHA-256, truncated/overflowing trailers, ENOSPC, rank/manifest file fsync, both directory barriers, publication failure and cancellation after linking. A child writer exits during a real partial manifest write without running cleanup; a new reader process then recovers the old epoch from disk.

At 1,000,001 tokens, the default TP2 FP8/no-target-head plus DFlash2 fixture restores **6,649,248,044 logical payload bytes** directly through a **2 MiB slab**, with **zero device allocations during `load`** and 1,954 destination page IDs (7,816 bytes). The long fixture uses the actual State/Plan and a recording sizing driver/synthetic reader; it proves geometry, page ownership and transfer/allocation bounds, not six GiB of physical NVMe I/O, checksum throughput or GPU performance. The smaller fixtures exercise actual files, checksum verification and byte copies through the real CUDA adapter with host-backed device memory. Test-oracle full byte arrays exist only in the small test fixtures, never in the restore implementation.

The existing `test-glm-snapshots-host` suite was run on an independent copy of the supplied base and on the changed source. Both have 22 passes and the same four baseline failures (two minimum-size comparisons and two expected cache hits). Their logs are retained. No broad `test-cuda-host`, real GPU/NVMe, live TP generation or power-cut qualification is claimed.

This is the coherent **metadata/direct-restore/filesystem-manifest milestone**. Server durable storage remains disabled. Remaining safe-point integration includes capture pause and page-write/COW guards across rewind/reset/fork; real TP capture, failure and cancellation agreement; bounded pinned-buffer admission and worker scheduling; rank persistence transport; all-rank restore success/failure and final activation; DFlash2 `resumeAt` and media lifecycle binding; durable epoch-ID allocation, quotas and garbage collection; and real GB10/NVMe recovery and generation qualification. An on-disk committed epoch or one rank's `Ready` alone does not authorize live inference activation.
