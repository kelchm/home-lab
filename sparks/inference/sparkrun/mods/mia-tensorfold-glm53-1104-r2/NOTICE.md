# Local changes

This separate `1104-r2` bundle adapts Mia's kit at `02dac3879852b8c5713318d3d64a5a791002e954`. It retains the pinned TensorFold base and upstream notices. Local additions provide SparkRun launch/supervision and capacity gates, independent upstream/site patch verification, off-host source preparation and the reviewed engine patches under `patches/`. Patch headers identify their basis; `0400` backports TensorFold #2103 with an adaptation for this kit's host fixtures. The chat template remains byte-identical to the selected kit. This development bundle does not establish production equivalence or authorize a rollout.

The upstream notice follows.

The local Dockerfile also builds FFmpeg 9.0.2 with GPL components disabled for the portable native video decoder. Its exact source archive hash and build configuration are retained in the image's `FFMPEG-BUILD-INFO`; its upstream licence files are copied under `LICENSES/FFmpeg`. DLPack, bundled with xgrammar, retains its Apache-2.0 terms and its licence is copied into the image. These additions do not change the licences of the upstream components below.

# NOTICE

GLM-5.3-Flash EXL3 on 2x DGX Spark with TensorFold's native engine
Copyright 2026 Mia's AI Lab (MiaAI-Lab). Licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).

This recipe builds on third-party work that keeps its own licence. If you redistribute it or a modified version, keep this
notice and the notices below, and state what you changed (Apache-2.0, section 4). People and projects are credited in
[CREDITS.md](CREDITS.md).

## What is in this repository, and what is not

This repository holds a recipe: scripts, a Dockerfile, a chat template and patches. It does not contain TensorFold, model weights
or binaries. The LICENSE above does not cover third-party work this recipe downloads, or change that work's terms.

## TensorFold

https://github.com/ashhart/TensorFold, by Ash Hart and the TensorFold contributors.

`patches/engine/*.patch` modify TensorFold's source at commit `f350cd3ea253a042e205746928271f9d8f71a82d` and contain parts of it. The
Docker build fetches that commit, applies them, and copies TensorFold's `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` and `LICENSES/` into
the image.

TensorFold's NOTICE reads:

    TensorFold
    Copyright 2026 TensorFold contributors
    https://github.com/ashhart/TensorFold

    TensorFold is licensed under the Apache License, Version 2.0 (LICENSE) from 0.6.0.
    Releases up to 0.5.0 were MIT-licensed, and code written before 0.6.0 keeps its
    MIT notice in LICENSES/MIT.txt. Third-party code and its licenses are listed in
    THIRD_PARTY_NOTICES.md.

TensorFold's MIT notice, for its code written before 0.6.0:

    MIT License

    Copyright (c) 2026 TensorFold contributors

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## Patches by other authors

Patches 0013, 0026-0029 and 0049-0056 are upstream TensorFold pull requests, carried with their authors' names in each patch's `From:`
line (see [CREDITS.md](CREDITS.md)). Those names must stay on them. Some were rebased to apply on this stack.

## DeepSeek-V4.1-Flash-1017-4x-DGX-Sparks-TensorFold

Pull requests #22, #24, #28 and #31 on that repository, by their authors (see [CREDITS.md](CREDITS.md)). Licensed under the Apache
License, Version 2.0.

**Changes:** patches 0092 and 0093 adapt its patches 0042 and 0039 to this repository's engine commit; the names in their `From:` lines
must stay on them. The checks in `start.sh` and `stop.sh` were rewritten for this recipe's scripts.

## GLM-5.3-Flash chat template

`templates/glm-5.3-flash-vision.jinja` is adapted from the GLM-5.3-Flash checkpoint's `chat_template.jinja`, under its MIT licence.

**Changes:** its media branch writes GLM's image and video markers instead of a note that it cannot see them.

    MIT License

    Copyright (c) 2026 Z.AI Co., Ltd

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## ExLlamaV3

This recipe's EXL3 code reads the EXL3 format of ExLlamaV3 (https://github.com/turboderp-org/exllamav3), MIT License,
Copyright (c) 2025 Turboderp. It contains no ExLlamaV3 code.

## Downloaded at build or first start, under their own terms

| What | Licence | Note |
|---|---|---|
| GLM-5.3-Flash EXL3 checkpoint, Mia's AI Lab, from Z.ai's GLM-5.3-Flash | Apache-2.0 for the quantization; Z.ai's MIT for the base model | Not in this repository |
| Ablit weights (`ABLIT=1`), Mia's AI Lab, from that checkpoint | MIT | Gated on Hugging Face; its terms apply. Not in this repository |
| **DFlash2 drafter weights, Inco AI** | **CC BY-NC-ND 4.0** | **Research and evaluation only; commercial use needs a licence from Inco AI. Not redistributed here** |
| xgrammar v0.2.8 | Apache-2.0 | Fetched and built into the image |
| libjpeg-turbo 3.1.4.1 | Its own BSD-style and zlib terms | Fetched and built into the image |
| libwebp 1.6.0 | BSD-3-Clause | Fetched and built into the image |
| Zig 0.17.0 | MIT | Build tool |
| NVIDIA CUDA images | NVIDIA's own terms | Deep Learning Container License; see each image's `/NGC-DL-CONTAINER-LICENSE` |

The image built by this recipe contains third-party software. If you redistribute that image, its terms apply as well as this notice.
