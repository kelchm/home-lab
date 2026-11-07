# Native video preparation: 0750

`../../patches/0750-native-video-single-pass.patch` makes the native GLM CUDA offer consume each clip synchronously during HTTP preparation. One decoder call reads every selected frame in order, resizes it once with the unchanged torch uint8 resampler, and retains compact fitted RGB. Only the unchanged normalization and float patch packing run in the active pair/window. The open decoder, encoded copy, index adapter, planning scratch and resampler allocations do not survive preparation. Deferred contexts contain no source or decoder callback.

The single decode call also reaches the declared final frame when it is not sampled. That validation frame is checked but is neither resized nor retained. This catches a truncated unsampled tail that the frozen eager sampler accepted. It does not add a visual sample, group, timestamp or token. The library's own interpretation of malformed containers and codec error concealment remains unchanged.

Sampling, fitted geometry, uint8 pixels, black padding, timestamps, odd-tail duplication and two-frame groups remain the 070/071 definitions. Limits remain four clips, 128 sampled frames, 16,384 tokens per clip, 32,768 shared video tokens, 16 MiB encoded per clip and 20 MiB encoded across clips. A validation-only tail can make the decoder's index list contain 129 entries while the visual sample cap remains 128. The existing shared 120-second preparation budget covers fetching, open/metadata, decoding and resampling; deadline checks occur before/after open and each selected-frame callback. The GLM offer no longer attaches that preparation budget to later float materialization. Cancellation checks in the active materializer remain intact.

## Composition and ownership

The patch applies cleanly after `0710` and before `0800`, `0820`, `0900`, and `0920`; the runner reverses the four later patches from the immutable runtime13 input and reapplies them in filename order. No hunk reconciliation was needed. The video-only `images.zig` wiring imports the `prepare_memory.zig` allocator introduced by 0900, so the complete composed bundle is the build target. The patch does not include 0760 or 0950; their owner composes those separately. No lock, build, launcher, route, normalization, finish-reason, lane-host, chat or warm-turn production file is changed.

The `VideoOffer.prepare_scratch` callback is optional and concerns videos only. `cuda_vision_offer.Video` uses it when the family exposes `prepareVideoWithScratch`; other offers keep their existing callback. `VideoOffer.lazy` now means deferred float patchification, with the video source borrowed only during preparation. `tfvideo.Video.framesWithScratch` charges the small C-index adapter to the supplied transient allocator; the original `frames` API remains available.

Group identity changes from namespace `0x676c6d76696403` to `0x676c6d76696404`. It includes the optional encoded-content identity, complete source/target/fitted geometry, patch/temporal/merge geometry, mean/std, pair indices, timestamps and actual fitted bytes. Caller identity alone no longer determines group identity; the tests distinguish different decoded content under the same caller identity. Old lazy/eager group identities intentionally do not match the new namespace.

Warm data URLs use the existing synchronous parent-thread preparation path and decode once for a fresh warm canvas set. The 0900 parent reference keeps the admission and parent arena until warm completion. The HTTP gate verifies both parent and warm consumption, zero decoding in engine callbacks, and zero final ledger bytes/residents. These are separate prepared prompt copies and can coexist; the 147 MiB bound below is per prepared prompt, not the sum of a parent and a warm copy. No remote speculative fetch path was added.

## Memory accounting

The checked bound comes from the actual `Plan`: `samples * content_h * content_w * 3 <= pairs * tokens_per_pair * 4 * 1176`. Each visual token therefore permits at most 4,704 retained uint8 bytes; 32,768 tokens permit exactly 154,140,672 bytes (147 MiB). Padding and an odd duplicate are implicit, and no request-wide float tensor is allocated. A plan that exceeds the actual token cap is rejected before pixel allocation.

The four-clip boundary test reaches that exact canvas payload with 128 frames per clip. The real 0900 retained allocator reports 202,309,812 bytes of arena capacity (about 193 MiB), including growth slack and descriptors; it is not mislabeled as 147 MiB. Media scratch peaks at 305,192 bytes in that no-resize boundary fixture and returns to zero before admission. Releasing the real `Request` returns every category to zero. Large canvases use the request's existing arena; this patch does not add an untracked retained allocator to hide its capacity overhead.

FFmpeg owns internal C allocations that its unchanged portable ABI does not expose to Zig. Their decoder/container lifetime is scoped to preparation and they are closed on success and failure. They are **not** represented as if they were 0900 allocator bytes. The actual-library gate records process RSS alongside the actual Zig retained/scratch counters. Its byte counter measures RGB delivered to the frame sink, not FFmpeg reference buffers or unsampled internal YUV frames. RSS is a process observation, not an isolated FFmpeg allocation total.

## Reproduce

Only host test executables and a generic ARM64 test executable run. The Linux test executable opens candidate-9 `libtfvideo.so` in a network-disabled Colima container; no inference executable, model, GPU, serving host or artifact rebuild is used. The supplied library's SHA-256 is `d65657f8683696b23cabe7c97c2812f900ffba332f51dbc94dff0f8a204338b1`.

Generate the moving 33-second 1080p fixture outside the repository, then run the complete gate:

```sh
python3 tests/video_prepare/make_long_fixture.py --output /tmp/video-0750-long.mp4
python3 tests/video_prepare/run.py \
  --source /tmp/glm1104-runtime13-locked-source \
  --zig "$HOME/.cache/zig-0.17.0-macos/zig" \
  --library "$HOME/.cache/glm1104-video-probe/candidate-9/libtfvideo.so" \
  --long-clip /tmp/video-0750-long.mp4
```

Run those commands from the mod directory. `--work-dir` can name a new disposable directory; it holds copied sources, logs, binaries and a receipt. Without `--long-clip`, the five-codec, HTTP, allocation and lifecycle gates still run, but the 1080p gate is omitted. `source-manifest.json` pins all 5,270 build/source inputs, the locked marker, later composition patches, the portable library and committed fixtures. The runner uses its own frozen runtime13 fixture installer, refuses input/hash mismatches, and verifies the immutable source and decoder library again after testing. `make_long_fixture.py` records the generator version, command and encoded hash; the recorded run produced 11,914,890 bytes using FFmpeg 9.0.2.

The eager float oracle is the unmodified `video_core.zig` copied from the immutable input under a separate module. The old 070 lazy control demonstrably decodes every pair again, while 0750 materialization does not call the source. Production lifecycle fixtures are updated to expect decode failures at preparation and still assert exact pixels, cancellation/deadline handling, retry and cleanup. An additional test drives actual CUDA window ownership with recording device operations across a straddling and repeated video pair.

The native HTTP tests cover positive Chat Completions, four-clip admission, five-clip rejection, invalid headers, short clips, truncated sampled/unsampled tails, malformed frame geometry, budget refusal, 16 MiB acceptance and one-byte overflow, disconnect, engine refusal/recovery, warm ownership and final zero ledger/residents. The locked Messages and Responses translators do not support video; those APIs are verified to refuse with HTTP 400 before decode or engine admission. Positive video/warm parity for those two APIs is not claimed and their normalization code is not changed.

The recorded long-clip gate validates 33 groups and compares 153,058,752 float values, including repeated materialization, with zero differences and zero repeat decoded bytes. Its CPU preparation timing is from a debug test build, not a production performance qualification. GPU pixels, GPU tower arithmetic, model quality and production serving remain unqualified.

## Final composed-source decoder gate

`run.py` reproduces the historical 0750 milestone against its frozen runtime13 input. `composed.py` instead verifies the selected bundle's complete prepared-source receipt, installs the same untouched real-library fixtures and eager oracle, and executes all 32 tests against the portable decoder exported from the selected candidate image. Missing libraries and skipped cases fail this gate. It also requires the recorded 1080p fixture.

```sh
python3 tests/video_prepare/composed.py \
  --source /absolute/final-prepared-source \
  --zig "$HOME/.cache/zig-0.17.0-macos/zig" \
  --library /absolute/candidate-export/libtfvideo.so \
  --image sha256:VERIFIED_CANDIDATE_DAEMON_ID \
  --long-clip /tmp/glm1104-video0750-1080p40.mp4
```

The generic broad host gate permits an absent FFmpeg installation and reports those cases as skipped. That result cannot replace the required decoder gate above.
