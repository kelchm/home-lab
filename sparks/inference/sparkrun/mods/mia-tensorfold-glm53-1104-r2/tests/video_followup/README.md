# Warm video reuse and sampled-frame compatibility

Patch `0940-native-video-warm-reuse.patch` corrects the independent review's two regressions in 0750. The parent request retains matching validated video canvases for its warm borrower. The match includes normalized media order, URLs, model context, preparation callbacks and frame/token policy. A warm expansion borrows those callbacks under the existing parent reference rather than decoding again or allocating another canvas set. Warm rendering now resolves media order as ordinary prompt rendering does.

Preparation requires all sampled frames, as the Python loader does. It does not demand an unsampled frame inferred from `nb_frames`: valid stream-copy trims can declare frames that their edit lists discard. The fixture declares 180 frames and supplies all 12 sampled frames; its prepared pixels match the frozen eager reference. Missing sampled frames, invalid headers, short clips and oversized requests remain refusals. Late inference classification covers the reachable media deadline; generic `Short`, `Decode` and `BadVideo` errors retain server-failure semantics instead of being misclassified as client video input.

```sh
python3 tests/video_followup/run.py \
  --source /tmp/glm1104-video15-locked-source \
  --zig "$HOME/.cache/zig-0.17.0-macos/zig" \
  --library "$HOME/.cache/glm1104-video-probe/candidate-15/libtfvideo.so" \
  --image sha256:c9a61e94a37686ed30ca294b447d3ba8a942746012a2afc9892c4821db862148 \
  --long-clip /tmp/glm1104-video0750-1080p40.mp4 \
  --negative-controls
```

The recorded focused host gate passes 153 tests and 20 build steps, including Linux native-open compilation. The required generic Linux decoder gate passes all 34 tests without skips. An SSE client reads to socket EOF while warm work remains outstanding, with exactly one decode and retained parent ownership. Both warm completion and refusal release the request's resident slot and allocator bytes. Two controls compile successfully and fail the intended tests: disabling reuse produces two decodes, and restoring the declared-tail requirement rejects the valid trimmed fixture with `Short`. The receipt distinguishes these assertions from compiler failures or timeouts.

The [manifest](source-manifest.json) pins the complete input-source marker, changed production files, decoder and fixtures. The [receipt](receipt.json) records actual commands and log hashes. The standalone runner uses a frozen fixture installer; the final composed bundle must pass its own complete gates as well. No serving executable, GPU, model or Spark was accessed.

The 147 MiB bound applies to canvas payload at the 32K video-token ceiling. Arena capacity depends on allocation history: the four-clip fixture retains about 193 MiB, while two larger clips can retain about 385 MiB before HTTP/JSON overhead. These are measured shapes, not universal request-memory bounds. Reuse removes the second warm canvas allocation. Codec C allocations remain outside the Zig ledger. Metadata-only video token counting, pre-admission disconnect handling, adversarial frame-planning bounds and GB10 memory/quality qualification remain separate work; this patch does not claim them complete.
