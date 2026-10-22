# Upstream TensorFold's native CUDA server for GB10 with this directory's patches, NCCL and the RDMA verbs it loads.
# Nothing in it is model-specific. build.sh supplies release/ (the checksum-verified archive) and patches/.
ARG DEVEL_IMAGE
ARG RUNTIME_IMAGE
FROM ${DEVEL_IMAGE} AS build
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl git python3 >/dev/null
ARG ZIG_VERSION
ARG ZIG_SHA256
RUN curl -fsSL -o /tmp/zig.tar.xz "https://ziglang.org/download/${ZIG_VERSION}/zig-aarch64-linux-${ZIG_VERSION}.tar.xz" && \
    echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz
ARG NCCL_REPOSITORY
ARG NCCL_REVISION
# The engine needs NCCL 2.30 or later; NVIDIA packages none for CUDA 13.0, so it is built here for sm_121 only.
RUN git init -q /nccl && cd /nccl && git remote add origin "https://github.com/${NCCL_REPOSITORY}.git" && \
    git fetch -q --depth 1 origin "${NCCL_REVISION}" && git checkout -q --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${NCCL_REVISION}" && \
    make -s -j"$(nproc)" src.build CUDA_HOME=/usr/local/cuda NVCC_GENCODE="-gencode=arch=compute_121,code=sm_121"
ARG TF_REPOSITORY
ARG TF_REVISION
RUN git init -q /tensorfold && cd /tensorfold && git remote add origin "https://github.com/${TF_REPOSITORY}.git" && \
    git fetch -q --depth 1 origin "${TF_REVISION}" && git checkout -q --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${TF_REVISION}"
COPY release /release
ARG TF_VERSION
# The release's build: glibc 2.28, baseline CPU, stripped, nvcc 13.0 kernels for its SMs (release_sms in
# zig/build/cuda.zig). Unpatched, it must equal the release byte for byte.
ARG ZIG_FLAGS="-Dtarget=aarch64-linux-gnu.2.28 -Dstrip=true -Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=80,86,120,121"
RUN cd /tensorfold && /opt/zig/zig build native ${ZIG_FLAGS} "-Dversion=${TF_VERSION}" --prefix /opt/reference -j"$(nproc)" && \
    cmp /opt/reference/native/bin/tensorfold-native /release/bin/tensorfold-native
COPY patches /patches
RUN cd /tensorfold && for p in /patches/*.patch; do git apply --check "$p" && git apply "$p" || exit 1; done && \
    /opt/zig/zig build native ${ZIG_FLAGS} "-Dversion=${TF_VERSION}" --prefix /opt/patched -j"$(nproc)"

FROM ${RUNTIME_IMAGE}
ARG RUNTIME_PACKAGES
# The base's NCCL 2.28 is below the engine's minimum. ibverbs-providers has the mlx5 provider for the CX7 ports, and
# SparkRun mods run in python3.
RUN apt-mark unhold libnccl2 >/dev/null && apt-get purge -y -qq libnccl2 >/dev/null && \
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ${RUNTIME_PACKAGES} >/dev/null && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /release /opt/tensorfold
COPY --from=build /opt/patched/native/bin/tensorfold-native /opt/tensorfold/bin/tensorfold-native
COPY --from=build /nccl/build/lib/ /tmp/nccl/
COPY --from=build /nccl/LICENSE.txt /opt/tensorfold/LICENSES/NCCL-LICENSE.txt
# The release's SHA256SUMS lists the unpatched server, so it goes.
RUN rm /opt/tensorfold/SHA256SUMS && cp -P /tmp/nccl/libnccl.so* /opt/tensorfold/lib/ && rm -rf /tmp/nccl && \
    echo /opt/tensorfold/lib > /etc/ld.so.conf.d/tensorfold.conf && ldconfig && \
    tensorfold_nccl=$(ldconfig -p | grep -c 'libnccl.so.2 ') && test "$tensorfold_nccl" = 1
ARG NCCL_VERSION
ENV PATH=/opt/tensorfold/bin:${PATH} NCCL_VERSION=${NCCL_VERSION}
ARG TF_REVISION
ARG TF_VERSION
ARG PATCH_HASH
LABEL org.opencontainers.image.source="https://github.com/ashhart/TensorFold" \
      org.opencontainers.image.revision="${TF_REVISION}" \
      org.opencontainers.image.version="${TF_VERSION}" \
      tf.patches="${PATCH_HASH}"
RUN tensorfold-native --version
