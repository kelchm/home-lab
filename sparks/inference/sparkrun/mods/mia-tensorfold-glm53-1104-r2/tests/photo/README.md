# Native photo parity qualification

`0650-native-photo-cmyk-parity.patch` adds CMYK/YCCK support to the existing `zig/src/core/picture/jpeg.zig` path. It calls the pinned TurboJPEG 3 API directly, requests `TJPF_CMYK` for four-component inputs, and compacts the decoded samples to RGB with Pillow's `CMYK;I` inversion and `Convert.c:cmyk2rgb` integer rounding. Libjpeg performs YCCK to CMYK. Pillow assumes inverted components even without Adobe APP14; changing inversion based on the marker would break parity. ICC profiles are not applied, matching ordinary Pillow `convert('RGB')`.

The patch changes only `jpeg.zig` and the existing picture regression's obsolete CMYK-refusal exception. RGB and gray still use their original direct pixel formats, accurate integer IDCT and smooth chroma upsampling. EXIF parsing, orientation, PNG/WebP decoding, alpha composition, server routing, GLM processing, normalization, memory lanes and checkpoint code are unchanged. The legacy `CmykJpeg` error declaration remains for caller compatibility; this decoder no longer emits it.

## Apply order and source identity

Start with the integrated native source before 065, such as the read-only `/tmp/glm1104-r2-integrated` supplied for this task. Existing site patches through 0600 must already be present. Apply `patches/0650-native-photo-cmyk-parity.patch` in filename order before the independent 070 media-memory and 080 checkpoint patches. No Dockerfile change is included. `source-manifest.json` records the patch hash, exact before/after hashes of both changed files, untouched decoder dependencies, fixture hash and Pillow reference source archive. The runner rejects a different decoder baseline rather than reporting its result under this receipt.

`run.py` copies source into a fresh scratch directory and uses `git apply` without initializing a Git repository. The supplied engine is read-only. It neither accesses a model host nor runs GPU code, and performs no checkout, branch, commit, push or publication. Docker execution is optional, uses only an existing local image with networking disabled, and sends a portable CPU test binary and fixtures over stdin.

## Reproduce

Use Python 3.13 and Zig 0.17.0. Install the reference into a scratch virtual environment using the hash-locked requirements. The runner requires Pillow **12.3.0**, its actual libjpeg-turbo **3.1.4.1**, and libwebp **1.6.0**. A current Pillow 13 wheel has libjpeg-turbo 3.2.0 and is refused as the oracle.

```sh
python3.13 -m venv /tmp/photo-reference
/tmp/photo-reference/bin/python -m pip install --require-hashes -r tests/photo/requirements.txt
python3 tests/photo/build_codecs.py --out /tmp/photo-codecs --fixture-source
```

Run these commands from the mod directory, or replace the relative `tests/photo` paths with absolute paths. The codec builder downloads release source archives, verifies SHA-256, builds shared CPU libraries with CMake, and records commands and artifact hashes in its output directory. It never clones a repository. On macOS the resulting libraries are `jpeg-build/libturbojpeg.0.dylib` and `webp-build/libwebpdecoder.3.dylib`; on Linux use `libturbojpeg.so.0` and `libwebpdecoder.so.3` in those build directories. CMake, a C compiler, platform build tools and network access to the archive URLs are needed for this optional preparation step. The test runner itself performs no downloads.

```sh
/tmp/photo-reference/bin/python tests/photo/run.py \
  --engine /tmp/glm1104-r2-integrated \
  --zig /Users/kelchm/.cache/zig-0.17.0-macos/zig \
  --jpeg /tmp/photo-codecs/jpeg-build/libturbojpeg.0.dylib \
  --webp /tmp/photo-codecs/webp-build/libwebpdecoder.3.dylib \
  --receipt /tmp/photo-receipt.json
```

To also execute against the actual Linux runtime codec libraries, append `--docker-context colima-tf-build --docker-image <existing-image-ID-or-digest>`. The runner cross-compiles a **generic aarch64 Linux CPU** binary with the supplied Zig, then runs it in that image. It expects the packaged libraries at `/opt/tensorfold/native/lib/{libturbojpeg.so.0,libwebpdecoder.so.3}` and records their hashes and the immutable local image ID. `--linux-target` can select another CPU architecture when the local image supports it. No serving binary or GPU is used. Runtime-image integration remains with the root task.

The runner first reopens every encoded fixture in the pinned Pillow environment and verifies complete RGB bytes and refusal outcomes against `cases.json`; it then tests the same bytes through the real native `picture.decode`, including `finish` orientation and alpha composition. It runs all existing picture regressions, allocator-failure cleanup tests, and a negative control with an explicitly missing JPEG library. It writes a PASS receipt only after every requested execution succeeds. Missing/wrong/incomplete codecs fail the run; none of the new pixel cases can skip. Compile-only work is not counted as pixel parity.

To regenerate fixtures independently of native decoder output:

```sh
/tmp/photo-reference/bin/python tests/photo/make_fixtures.py \
  --jpeg /tmp/photo-codecs/jpeg-build/libturbojpeg.0.dylib \
  --pillow-source /tmp/photo-codecs/source/pillow_fixture/pillow-13.0.0 \
  --out /tmp/photo-cases-regenerated.json
cmp tests/photo/cases.json /tmp/photo-cases-regenerated.json
```

Pillow 13.0.0's hash-verified **source archive supplies only an independent, unmodified YCCK input**: `Tests/images/pil_sample_cmyk.jpg`, SHA-256 `1086f923d60bb5d6c4f2d3f7f56ba5d315d98e6f19ee8c8797dabac630c8ae80`. Its output oracle is still Pillow 12.3.0 with the exact codec pin. `PILLOW-LICENSE` covers that fixture. Other inputs come from Pillow's independent encoders or direct pinned TurboJPEG compression calls. No expected RGB bytes are computed using the implementation's conversion function.

## Evidence and boundaries

The checked-in receipt records **29/29 tests passed on macOS ARM64 and 29/29 on Linux ARM64**, with **49 full pixel-array comparisons, 10 matching refusals and zero skips** on each platform. This includes the original golden picture, edge-picture, WebP and damaged-input regressions. Allocation-failure enumeration covers real CMYK EXIF decoding, progressive YCCK decoding, output allocation, and post-decode malformed-EXIF rejection. Caller-owned allocations are checked by Zig's testing allocator; codec handles are destroyed with `defer`, including on error. The negative control fails with `NoJpegLibrary` as required. `receipt.json` records executable, patch, test-source, fixture, source-file and loaded-library hashes; scratch directories retain complete logs.

The dedicated 59 fixtures cover:

- Adobe transform-0 CMYK with inverted samples; the same compressed samples without APP14; direct-codec non-inverted CMYK without APP14; baseline/progressive Adobe transform-2 YCCK; and the independent Pillow YCCK input.
- RGB JPEG with 4:4:4, 4:2:2 and 4:2:0 sampling, baseline/progressive gray, and EXIF orientations 1–8 for RGB, gray and CMYK. Malformed CMYK EXIF is tested both with and without JFIF DPI, preserving Pillow's different outcomes.
- RGB/RGBA/LA PNG, palette PNG with partial alpha, lossy/lossless RGB and RGBA WebP, and WebP alpha plus EXIF orientation. WebP implementation is unchanged.
- A valid two-frame MPO with distinct pixels in frame two, EXIF orientation on the first image, and first-image output verified against Pillow. This does not decode or select later MPO frames.
- Missing EOI and mid-stream truncation of RGB, CMYK and progressive JPEG, truncated PNG/WebP, and a valid JPEG beyond the side limit.

Supported JPEGs are the existing 8-bit one-, three- and now four-component inputs accepted by pinned libjpeg-turbo, including CMYK/YCCK and first-image MPO. The existing dimensions remain **8192 per side and 16,777,216 pixels**. Four-component decoding uses at most **64 MiB** of caller-owned sample storage, compacts in place without a second CMYK buffer, and uses the existing oriented RGB output allocation (at most another **48 MiB**). Gray/RGB sample allocation stays at three bytes per pixel. A codec `MAXPIXELS` gate duplicates the checked dimensions; `MAXMEMORY=256` MiB bounds libjpeg's progressive/intermediate coefficient buffers. That parameter is not a total process RSS cap: input storage, EXIF accumulation, codec row buffers and the existing one-time library-version probe are separate. Encoded-byte/HTTP-body admission remains the caller's responsibility.

This does not add 12-/16-bit JPEG, color-managed ICC rendering, arbitrary multipage decoding, GIF/TIFF, animated WebP or APNG support. Existing invalid/truncated handling remains in effect; it is not a claim of identical behavior for every malformed JPEG or unusual MPO index. Pathological progressive inputs exceeding the new codec intermediate-memory budget are refused even when a less bounded Pillow process might decode them. No runtime rollout, GPU validation or server-level acceptance is claimed.
