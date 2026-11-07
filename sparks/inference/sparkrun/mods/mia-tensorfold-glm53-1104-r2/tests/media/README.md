# Native GLM media milestone 030

`0300-native-glm-media-contract.patch` targets the supplied patched106 engine at upstream commit `f350cd3ea253a042e205746928271f9d8f71a82d`. Mia108 has the same relevant source. The engine directory is an input; all implementation and test compilation used disposable file copies. This patch does not change a model host, launcher, request-body routes, branches, or GPU row allocation.

Patch SHA-256: `97fef25bf4fc0712216f885decf7af231844a26e6044c706546b9e3ebb2ffa53`.

## Implemented behavior

- GLM's native offers select a fixed 2048-token ceiling per image, independently of the configured total image-token budget. Adding images does not divide the budget of earlier history images. Explicit `detail: low` retains the existing 256-token ceiling. The launcher must configure `--vision-image-tokens 102400 --vision-max-images 50`; actual prepared tokens above the total are refused, never reduced or pruned. Other model families retain their existing shared-budget policy.
- GLM request image limits are 10 MiB encoded per image, 64 MiB aggregate encoded bytes, 8192 pixels per side, 16 × 1024² pixels per image, and 400 × 1024² aggregate decoded pixels. Every occurrence counts, including repeated URLs and images already represented in a prompt cache. The production decoder enforces individual dimensions; GLM planning reports source pixels to the active `prompt.zig -> images.expand` path. `vision_inputs.zig` is untouched.
- The GLM image validation/fetch phase has a cooperative 100-second deadline checked before and after each image. Downloads share that deadline. A decoder operation already in progress is checked when it returns. Deferred patch preparation on the engine thread is outside this initial deadline and needs the next lifecycle layer to carry a deadline/cancellation context through it.
- Exact media occurrences travel through the production template using unique request-owned labels immediately preceding each image/video part. Labels are removed with provenance preserved, and the stripped render must equal the original render byte for byte. The resulting occurrence order determines extraction. Dropped, duplicated, or untraceable occurrences are refused. This follows GLM tool-result sorting without duplicating its sorting algorithm. Client messages are copied, not mutated.
- GLM image planning validates/decode-checks one image in temporary memory and retains its encoded bytes plus a deferred callback. CUDA and Metal image loops materialize float patches in an allocator scoped to one image. The ordinary eager image helper remains available to existing tools. These loops still encode all uncached image outputs at the start of a prompt pass; they do not yet prepare at prompt-chunk boundaries.
- GLM image resize now uses the existing Torch uint8 bicubic path. The native identity remains conservative: encoded bytes, processor revision, normalization, fitted content dimensions, and grid. Changing to Torch changes the identity namespace, preventing reuse of old Pillow-derived entries. Native identities are not Python fitted-canvas identities; equivalent pixels in differently encoded files can still cause cache misses.
- GLM video offers and native preparation cap each clip at 16384 visual tokens and 128 sampled frames. Per-clip ceilings do not divide by the number of clips. Actual prepared group rows are counted against the configured request total and excess is refused. The launcher must configure `--vision-video-tokens 32768 --vision-max-videos 4`. Video decoding and float patch preparation remain eager.

## Source ownership

The patch changes `zig/src/server/{prompt,images}.zig` and adds `media_order.zig`; extends `core/engine_vision.zig` and `core/lanes/stream.zig`; changes GLM `image_core.zig`, `image.zig`, `cuda_images.zig`, `slot_images.zig`; and wires the native CUDA offer and Metal GLM host adapters. It does not edit server normalization or API routes.

`0200-native-api-parity.patch` also edits `prompt.zig`, adding `canCloseThinking`. Applying 020 followed by 030 was checked and tested without conflicts. Apply each with `git apply --check PATCH` and then `git apply PATCH` to a disposable engine source copy. The bundle integrator owns subsequent 040/050/060 layers and the final source/binary identity.

## Run the executable CPU gate

From this mod's directory, with the known local Zig 0.17.0 executable supplied explicitly:

```sh
python3 tests/media/run_tests.py --engine /path/to/patched106/engine --zig /path/to/zig-0.17.0/zig --cpu apple_m1 --with-api --compile-native
```

The runner copies `zig/`, `tools/`, and the build files, checks and applies 020 then 030, installs the standalone Zig tests and actual bundled `chat_template.jinja`, and adds one temporary build step. It prints patch hashes and the scratch path. Omit `--with-api` when testing 030 alone against the original supplied base. Omit `--compile-native` for the CPU execution gate alone. On a non-Apple CPU, omit `--cpu apple_m1`.

Observed result for the combined 020+030 gate: **23/23 tests passed, 9/9 build steps succeeded**. Eight explicitly named media regression cases run through production code; the rest are aggregate test imports. Both `compile-glm-native-open` and `compile-omni-video-host` compiled the actual Linux CUDA startup/offer bodies with inert GPU images. Log: `/tmp/glm-native-media-milestone.log` on the implementation machine. These compile checks perform no GPU execution.

The executed cases cover all three API translators plus the production GLM template with equal-sized distinct reversed A/B tool images; fifty independent 2048-token budget admissions; history-budget stability; explicit occurrence/token/encoded-byte/pixel/time refusals; and source label collision avoidance. The byte/pixel/token admission tests use injected processors or fetch callbacks to exercise the real production admission branches without allocating full model inputs. The order tests use the real PNG decoder and real GLM image planning/materialization.

Pixel evidence includes exact 7×5 RGB PNG decoding, byte-for-byte Torch bicubic uint8 upsize/downsize fixtures, eager/deferred GLM image equivalence, and actual GLM pattern-image patches against an independently generated Torch float32 fixture at absolute tolerance `1e-6`. `fixtures/REFERENCE.txt` records Torch version and generation details. The video gate tests the production per-clip budget/accounting helpers and actual GLM frame-sampling/geometry planner; it does not decode real clips or execute a tower. No JPEG, WebP, EXIF, alpha, CMYK, MPO, PDF, checkpoint, or GPU pixel parity claim is made by this gate.

## Required next integration layer

1. Replace the unchanged fixed CUDA feature-row allocation and admission reservation with bounded active media rows at chunk boundaries. With 102400 image + 32768 video tokens the current reservation remains **1056 MiB per stream**. It is mandatory capacity work, not solved by deferring CPU image patches.
2. Implement video frame/group scoped preparation and bounded decoder ownership. Current video groups retain eager float patches. Four-clip configuration is available but four-clip throughput, memory, codec behavior, and numerical fidelity are unqualified.
3. Carry lazy preparation deadlines, cancellation, replay, failure cleanup, and source ownership through interleaving, resume, and cache/persistence lifecycles. Validate fresh versus resumed output, A/B/A interleaving, cancellation/reset, and disk reload with actual checkpoints and GPUs. New image hash namespaces prevent old image key matches, but are not a persistence qualification receipt.
4. Integrate the deliberately configured 96 MiB request-body limit and packaged pinned `libtfvideo`/FFmpeg decoder in the root layer. The baseline 32 MiB HTTP body limit remains until that integration.
5. Run live image/video tower and model gates, measured peak host/device/pinned memory, multi-stream admission, four-clip behavior, and 50-image history/resume checks on the target GPUs. Metal adapter changes also need backend compilation/device validation. CMYK/MPO decoding gaps remain; PDF is optional and unchanged.
