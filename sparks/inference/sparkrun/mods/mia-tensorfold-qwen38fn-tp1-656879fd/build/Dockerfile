# Mia's one-Spark Qwen3.8-Flash-Next image: TensorFold's zig-flashnext engine with the kit's patches and this mod's,
# its TP=1 kernel set, and the vision helper, on NVIDIA PyTorch 26.07. The stages follow the kit's scripts/prepare.sh;
# build.sh supplies the engine, the kit's patches and this mod's.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
ARG ZIG_VERSION
ARG ZIG_SHA256
RUN curl -fsSL -o /tmp/zig.tar.xz "https://ziglang.org/download/${ZIG_VERSION}/zig-aarch64-linux-${ZIG_VERSION}.tar.xz" && \
    echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz
COPY tensorfold /tensorfold
ARG ZIG_CPU
# The kit builds on a Spark with the host's CPU; here the Spark's is named, so the build host's never leaks in.
RUN cd /tensorfold && /opt/zig/zig build -Dnvcc=/usr/local/cuda/bin/nvcc -Doptimize=fast "-Dcpu=${ZIG_CPU}" \
      --prefix /opt/tensorfold -j"$(nproc)" fatbins install native && \
    test -x /opt/tensorfold/native/bin/tensorfold-native
RUN mkdir -p /opt/tensorfold/share/doc/tensorfold && cd /tensorfold && \
    for f in LICENSE LICENSE.md LICENSES NOTICE THIRD_PARTY_NOTICES.md; do \
      if [ -e "$f" ]; then cp -a "$f" /opt/tensorfold/share/doc/tensorfold/ || exit 1; fi; done
ARG KERNEL_SOURCE_MTIME
RUN cd /tensorfold && python -B -c 'import json, os, sys; t = int(sys.argv[1]); \
      [os.utime(os.path.join("src", f), (t, t)) for f in sorted({k["source"]["file"] for k in \
       json.load(open("zig/tests/cuda/flashnext/kernels.json"))["kernels"]})]' "${KERNEL_SOURCE_MTIME}" && \
    PYTHONPATH=/tensorfold/src python -B tools/zig/flashnext_aot.py build \
      --spec zig/tests/cuda/flashnext/kernels.json --jit zig/tests/cuda/flashnext/jit.json --tp 1 \
      --out /opt/tensorfold/share/tensorfold/cuda/sm121 > /tmp/aot.log || true && \
    cat /tmp/aot.log && \
    grep -q 'kernels ->' /tmp/aot.log && \
    bad=$(grep PROBLEM /tmp/aot.log | grep -v 'cubin sha256' || true) && \
    test -z "$bad" && \
    test -f /opt/tensorfold/share/tensorfold/cuda/sm121/aot.json

FROM ${BASE_IMAGE}
COPY --from=build /opt/tensorfold /opt/tensorfold
COPY --from=build /tensorfold/src/tensorfold /opt/tensorfold/python/tensorfold
# The kit's huggingface_hub>=1.0 is the base's own; its other two packages go in without dependencies, as the kit's do.
ARG PIP_PACKAGES
RUN ln -s /opt/tensorfold/native/bin/tensorfold-native /usr/local/bin/tensorfold-native && \
    python -c 'import huggingface_hub as h; assert int(h.__version__.split(".")[0]) >= 1, h.__version__' && \
    pip install --no-cache-dir --no-deps ${PIP_PACKAGES}
ARG KIT_REVISION
ARG PATCHES_HASH
LABEL tf.kit.revision=${KIT_REVISION} tf.patches=${PATCHES_HASH} tf.kernels=present
# The binary targets the Spark's CPU, so the build host cannot run it; the runbook runs it once on a Spark.
ENV TENSORFOLD_CUDA_KERNELS=/opt/tensorfold/share/tensorfold/cuda/sm121 \
    TENSORFOLD_VISION_PYTHONPATH=/opt/tensorfold/python
