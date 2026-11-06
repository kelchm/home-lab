# Credits

This recipe is by [Mia's AI Lab](https://huggingface.co/Mia-AiLab) ([MiaAI-Lab](https://github.com/MiaAI-Lab)). It builds on the
work below. Licence terms and required notices are in [NOTICE.md](NOTICE.md); this repository's own licence is in [LICENSE](LICENSE).

## The engine

**[TensorFold](https://github.com/ashhart/TensorFold)** by Ash Hart and its contributors: the inference engine, native Zig/CUDA
server, EXL3 kernels, speculative verification and API. This repository does not contain the engine. Its build fetches TensorFold at
commit `f350cd3e` and applies `patches/engine/` to it. TensorFold's own
[README](https://github.com/ashhart/TensorFold#readme) and
[third-party notices](https://github.com/ashhart/TensorFold/blob/main/THIRD_PARTY_NOTICES.md) list its contributors and sources.
The native EXL3 experts that several patches here change build on [@justincasey](https://github.com/justincasey)'s
[#837](https://github.com/ashhart/TensorFold/pull/837).

## Patches by other authors

These are upstream TensorFold pull requests, carried with their authors' names in each patch's `From:` line. Their patches remain theirs.

| Patches | Author | Upstream PR | What |
|---|---|---|---|
| 0013 | Adam Hillson ([@BadAd84](https://github.com/BadAd84)) | [#1623](https://github.com/ashhart/TensorFold/pull/1623) | DFlash2 reads complete prompt taps at TP>1 |
| 0026-0027 | [@ajensenwaud](https://github.com/ajensenwaud) | [#1505](https://github.com/ashhart/TensorFold/pull/1505) | Load-ahead in the grouped EXL3 experts kernel |
| 0028-0029 | Adam Hillson ([@BadAd84](https://github.com/BadAd84)) | [#1534](https://github.com/ashhart/TensorFold/pull/1534) | EXL3 decode reads the trellis through `ld.global.nc.L1::no_allocate` |
| 0049-0056 | [@Bizuayeu](https://github.com/Bizuayeu) | [#1423](https://github.com/ashhart/TensorFold/pull/1423) | Paired prompt chunks |

Patches 0057-0063 are Mia's AI Lab's extension of #1423 to DFlash2.

## The port to 2x DGX Spark

This recipe is the lab's [2x RTX PRO 6000 recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold) moved to GB10. The engine, the first 93 patches and the serving shape are
that recipe's (0103 and 0104 were ported from it later, by the lab, 0103 with a guard); 0094-0102 are this pair's own; the two-host launch, the ConnectX-7 link discovery and the unified-memory accounting are adapted from Mia's
AI Lab's **[GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold)**
(branch `zig-engine`): `scripts/nodes.sh`'s `link_info`, `rails`, `rail_info`, `rail_ping` and `cx7_peer`, and the
memory rule in `scripts/memavail.sh`. The hard-won parts of that code are its: that the GID index must be the RoCE v2
entry holding the node's own IPv4 address, and that a Spark's single QSFP port reaches the GB10 over two PCIe links and
so needs both of its RDMA devices given to NCCL.

The engine's own RoCE all-gather, which this recipe turns on for the pair, is the GPU side of b12x's "RoCEnante"
exchange ([local-inference-lab/b12x](https://github.com/local-inference-lab/b12x), `b12x/comm/roce`, Apache
License 2.0), carried upstream in TensorFold as `zig/src/cuda/roce.zig`.

## Work this recipe adapts

- **[GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash)** by Z.ai (MIT): `templates/glm-5.3-flash-vision.jinja` is the
  checkpoint's own chat template with its media branch changed.
- **[DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold](https://github.com/MiaAI-Lab/DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold)**
  pull requests: patch 0092 adapts [@SvangenStudios](https://github.com/SvangenStudios)'s
  [#31](https://github.com/MiaAI-Lab/DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold/pull/31) and patch 0093 part of their
  [#28](https://github.com/MiaAI-Lab/DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold/pull/28), carried with their name in each
  patch's `From:` line. `stop.sh`'s warning about open requests and `start.sh`'s stricter smoke test follow their
  [#24](https://github.com/MiaAI-Lab/DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold/pull/24), and `start.sh`'s log follower closing
  the lock's descriptor follows [@tfolkman](https://github.com/tfolkman)'s
  [#22](https://github.com/MiaAI-Lab/DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold/pull/22).

## Ideas and formats

- **[ExLlamaV3](https://github.com/turboderp-org/exllamav3)** by turboderp (MIT): the EXL3 format, and an experts-kernel technique that
  patch 0026 cites.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp)**: PR #22522's results on this card class were the evidence for trying programmatic
  dependent launches. No code was taken.

## Models (not in this repository)

- **[Z.ai](https://huggingface.co/zai-org/GLM-5.3-Flash)**: GLM-5.3-Flash, its architecture and base weights (MIT).
- **[Mia's AI Lab](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold)**: the EXL3 checkpoints, including the gated
  [Ablit weights](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit).
- **[Inco AI](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2)**: DFlash2 drafter weights, **CC BY-NC-ND 4.0, for research and
  evaluation**. Downloaded by the user, never redistributed here.

## Build and runtime

[NVIDIA CUDA](https://developer.nvidia.com/cuda-toolkit) and its container images, [Zig](https://ziglang.org),
[xgrammar](https://github.com/mlc-ai/xgrammar), [libjpeg-turbo](https://libjpeg-turbo.org) and
[libwebp](https://developers.google.com/speed/webp), under their own licences.

## How it was made

Most of Mia's AI Lab's patches were written by AI coding agents under the lab's direction, with reviews by Codex, DeepSeek
and GLM-5.3-Flash.
