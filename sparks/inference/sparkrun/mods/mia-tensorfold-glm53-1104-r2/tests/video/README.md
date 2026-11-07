# Portable video decoder gate

The Dockerfile builds FFmpeg 9.0.2 from its checked SHA-256, enables H.264/HEVC/VP8/VP9/MPEG-4 with MOV/MP4/Matroska/WebM containers, and links the native `libtfvideo` ABI with a generic CPU target. The inference engine retains its Spark CPU target. Never execute that serving binary on the workstation. The codec gate runs only the portable library and a small C driver, without CUDA, network access, model weights or a model endpoint.

Generate fixtures with the workstation FFmpeg and record its exact version plus source/encoded hashes:

```sh
python3 tests/video/make_fixtures.py --output /absolute/new/fixture-directory
```

Export `native/lib/libtfvideo.so` from the exact candidate image without starting its server. Supply that file to the gate with an off-host ARM64 compiler image from this Dockerfile's `ffmpeg` stage:

```sh
python3 tests/video/run_smoke.py --library /absolute/libtfvideo.so --fixtures /absolute/fixture-directory --toolchain-image COMPILER_IMAGE
```

The runner verifies the five fixture hashes, resolves and records the compiler image's immutable daemon ID and the decoder library's SHA-256, and sends its bounded inputs over Docker stdin. No host filesystem sharing is required. The compiler container uses `--network none` and is removed after the run.

The functional gate checks stream dimensions/rate/count, selected frame indices, approximate RGB colours against independently generated source pixels, repeated decoding on one handle, callback cancellation followed by a successful decode, and invalid input refusal. The eight-level RGB tolerance allows lossy codecs. This is not bit-exact Python/PyAV pixel parity, video tower quality, memory qualification, or live inference evidence. The first local gate used FFmpeg 9.0.2 and passed all five codecs against a portable GCC build of the exact native ABI source; the final Zig-linked image library needs its own receipt.
