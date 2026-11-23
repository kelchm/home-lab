# GLM Python-compatible verification weights on Mia’s Zig stack

Place this directory beside `prompt-s2/` under the frozen Mia mod. The builder reads the parent lock for Mia’s kit revision, archive checksum, engine revision, CUDA image digests, Zig compiler checksum, model and drafter pins. It verifies the loaded MLA parent image, applies the original 16 kit patches and the five local patches in filename order, and checks the reconstructed arm64 image against `variant.json` before giving it the qualified tag.

```sh
python3 python-quant/build.py --kit-archive /path/to/verified-mia-kit.tar.gz
```

The archive argument is optional. Without it the builder downloads the pinned source archive. Supply `--mod-dir` when running from another directory layout. The build requires Python 3.11 or later with tarfile data filters, Docker BuildKit, and the already loaded `glm53-tf:1104-6c55c20-s2-mla-e0e96fd943d7` arm64 image.

For the production-precision comparison, use `TF_GLM_MM=1`, `TF_GLM_DENSE_BITS=q4-mse`, `TF_GLM_HEAD_BITS=fp8`, `--kv-cache fp8`, and `TF_GLM_KV_POW2=1`. These select the Python loader’s affine group-64 clipped-MSE dense weights, block-128 E4M3 target head with FP32 scale, and power-of-two E4M3 KV rows. The EXL3 experts and BF16 MLA kv_b matrices come from the same checkpoint. The DFlash2 draft head and its gate and up projections use group-64 affine min–max Q4 on both stacks. In this image the five external drafter MLP down projections use BF16 weights. The Python loader packs those five projections to group-64 affine min–max Q4. For a BF16 target-weight control, set both native `*_BITS` options to `16` and keep the same cache format. The optional `TF_GLM_DFLASH_CALIBRATED=1` changes the greedy proposal selector independently of quantization.

The executable comprises 21 patches. The host-controls stage adds `prompt-s2/mla-host-tests.patch` and `host-controls.patch` only to its separate test tree. In that tree the combined command is

```sh
zig build -j3 test-glm-quant-ranks-host test-glm-mm-policy-host test-glm-fp8-kv-host test-glm-index-host test-glm-dflash-host test-glm-prompts-host test-glm-mla-port-host --summary all
```

The numerical receipt compares the shipping fatbin’s quantized bytes, scales and biases with the installed Python quantizer on real weights and synthetic inputs. It also checks multiple matrix row counts and split-K paths against dequantized FP32 matrix multiplication. These checks qualify target-weight representation independently of the throughput workload.

Extract the MM fatbin from the native executable with `python3 verify-weights.py --binary tensorfold-native --fatbin glm_mm.fatbin`. Run `python3 verify-weights.py --fatbin glm_mm.fatbin --model-dir /path/to/model` on a Spark in the matching TensorFold Python container.

BuildKit records the creation time in the image configuration and in the two filesystem layers. A warm build can reuse those timestamps. For a cold build, pass `--go /path/to/go1.26/bin/go` if Go 1.26 is outside your PATH. The builder verifies the rebuilt executable and BUILD-INFO against the qualification record, restores the recorded tar headers, and recompresses the two layers with the same Go 1.26 gzip implementation used by Docker 29.5.2. It verifies each compressed layer digest and imports the original arm64 OCI manifest on the pinned parent. The 14 KiB OCI record contains image metadata and tar headers. The executable and its CUDA kernels come from the new source build.

The independent full compile initially produced SHA-256 `5d1df4621275b3c4f39221195f953e7869021b86bdac36b9b729f4ce31b0a0f8`. It differed from the qualified executable in six bytes, all decimal digits in two NVCC-generated private C++ symbol names within the `qmm` and `qmm_group` CUDA archives. When those names differ, the builder restores their recorded spellings and requires the entire executable to match SHA-256 `f15844edd38ab468772c3972dc70f191e31b399bce3943071a08b890c228df7b` before packaging. This recovered the qualified ELF, both original OCI layer digests, arm64 manifest and config from the independent source compile. A one-byte payload change outside those symbols failed verification.
