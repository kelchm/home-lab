# Adapted from Mia's 1104 docker/Dockerfile at 02dac3879852b8c5713318d3d64a5a791002e954.
# The lock supplies digest-pinned bases and the Spark CPU; builds run only on the workstation.
# Changes: immutable base arguments, Python for SparkRun mods, and no execution of Spark CPU binaries on the Mac.
ARG BASE_IMAGE
ARG RUNTIME_IMAGE

# ---------------------------------------------------------------------------------------------------------------------
# Toolchain: CUDA devel (nvcc 13.3, g++ 13 as nvcc's host compiler) plus git/curl/cmake/nasm and Zig.
FROM ${BASE_IMAGE} AS toolchain
ARG ZIG_VERSION=0.17.0
ARG ZIG_SHA256
ARG ZIG_ARCH=aarch64
ARG ZIG_URL=https://ziglang.org/download/${ZIG_VERSION}/zig-${ZIG_ARCH}-linux-${ZIG_VERSION}.tar.xz
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
# libibverbs-dev is not linked (roce.zig dlopens libibverbs), but its presence keeps a verbs-aware build option honest.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git xz-utils cmake nasm libibverbs-dev \
 && rm -rf /var/lib/apt/lists/*
RUN test -n "${ZIG_SHA256}" || { echo "ZIG_SHA256 build arg is required" >&2; exit 1; } \
 && curl -fsSL --retry 3 -o /tmp/zig.tar.xz "${ZIG_URL}" \
 && echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/zig \
 && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 \
 && rm /tmp/zig.tar.xz \
 && ln -s /opt/zig/zig /usr/local/bin/zig \
 && test "$(zig version)" = "${ZIG_VERSION}"

# ---------------------------------------------------------------------------------------------------------------------
# Pinned image decoders for --vision: the server refuses JPEG/WebP inputs unless it finds exactly libjpeg-turbo
# 3.1.4.1 and libwebp 1.6.0 (zig/src/core/picture/{jpeg,webp}.zig); Ubuntu's packages are other releases.
# They are installed in lib/ beside the server, where it looks first. PNG needs no library.
# Both build from source on aarch64; libjpeg-turbo uses its own NEON paths there and never calls nasm.
FROM toolchain AS codecs
ARG JPEG_TURBO_VERSION=3.1.4.1
ARG JPEG_TURBO_SHA256=ecae8008e2cc9ade2f2c1bb9d5e6d4fb73e7c433866a056bd82980741571a022
ARG WEBP_VERSION=1.6.0
ARG WEBP_SHA256=e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564
WORKDIR /build
RUN curl -fsSL --retry 3 -o jpeg.tar.gz \
      "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/${JPEG_TURBO_VERSION}/libjpeg-turbo-${JPEG_TURBO_VERSION}.tar.gz" \
 && echo "${JPEG_TURBO_SHA256}  jpeg.tar.gz" | sha256sum -c - \
 && mkdir jpeg && tar -xzf jpeg.tar.gz -C jpeg --strip-components=1 \
 && cmake -S jpeg -B jpeg/out -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/codecs -DCMAKE_INSTALL_LIBDIR=lib \
      -DENABLE_SHARED=ON -DENABLE_STATIC=OFF -DWITH_TURBOJPEG=ON -DWITH_JAVA=OFF -DWITH_TOOLS=OFF -DWITH_TESTS=OFF \
 && cmake --build jpeg/out -j"$(nproc)" --target turbojpeg \
 && install -D -m 0755 "$(readlink -f jpeg/out/libturbojpeg.so.0)" /codecs/lib/libturbojpeg.so.0 \
 && install -D -m 0644 jpeg/LICENSE.md /codecs/LICENSES/libjpeg-turbo-LICENSE.md
RUN curl -fsSL --retry 3 -o webp.tar.gz \
      "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-${WEBP_VERSION}.tar.gz" \
 && echo "${WEBP_SHA256}  webp.tar.gz" | sha256sum -c - \
 && mkdir webp && tar -xzf webp.tar.gz -C webp --strip-components=1 \
 && cmake -S webp -B webp/out -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
      -DWEBP_BUILD_ANIM_UTILS=OFF -DWEBP_BUILD_CWEBP=OFF -DWEBP_BUILD_DWEBP=OFF -DWEBP_BUILD_GIF2WEBP=OFF \
      -DWEBP_BUILD_IMG2WEBP=OFF -DWEBP_BUILD_VWEBP=OFF -DWEBP_BUILD_WEBPINFO=OFF -DWEBP_BUILD_WEBPMUX=OFF \
      -DWEBP_BUILD_EXTRAS=OFF -DWEBP_BUILD_LIBWEBPMUX=OFF \
 && cmake --build webp/out -j"$(nproc)" --target webpdecoder \
 && install -D -m 0755 "$(readlink -f webp/out/libwebpdecoder.so.3)" /codecs/lib/libwebpdecoder.so.3 \
 && install -D -m 0644 webp/COPYING /codecs/LICENSES/libwebp-COPYING \
 && ldd /codecs/lib/libturbojpeg.so.0 /codecs/lib/libwebpdecoder.so.3

# Video uses the native decoder's exact FFmpeg pin, matching the engine's build/video.zig.
# Keep the codec build independent of engine patches so it remains cached while native code changes.
FROM toolchain AS ffmpeg
ARG FFMPEG_VERSION=9.0.2
ARG FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
RUN apt-get update && apt-get install -y --no-install-recommends make gcc g++ pkg-config \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build/ffmpeg
RUN curl -fsSL --retry 3 -o source.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
 && echo "${FFMPEG_SHA256}  source.tar.xz" | sha256sum -c - \
 && mkdir source && tar -xJf source.tar.xz -C source --strip-components=1 \
 && cd source \
 && ./configure --prefix=/ffmpeg --cpu=generic --enable-static --disable-shared --enable-pic \
      --disable-programs --disable-doc --disable-network --disable-autodetect --disable-everything \
      --disable-avdevice --disable-avfilter --disable-swresample \
      --enable-avcodec --enable-avformat --enable-swscale \
      --enable-decoder=h264,hevc,vp8,vp9,mpeg4 \
      --enable-parser=h264,hevc,vp8,vp9,mpeg4video --enable-demuxer=mov,matroska \
      --enable-bsf=vp9_superframe_split \
 && make -j8 && make install \
 && mkdir -p /ffmpeg/LICENSES \
 && cp COPYING* LICENSE.md /ffmpeg/LICENSES/ \
 && { echo "ffmpeg=${FFMPEG_VERSION} sha256=${FFMPEG_SHA256}"; cat ffbuild/config.mak; } > /ffmpeg/BUILD-INFO

# ---------------------------------------------------------------------------------------------------------------------
# Engine build: TensorFold at TF_REF, patches/engine/*.patch applied in filename order, kernels for SM only.
FROM toolchain AS build
ARG XGRAMMAR_REPO=https://github.com/mlc-ai/xgrammar.git
ARG XGRAMMAR_TAG=v0.2.8
ARG XGRAMMAR_COMMIT=97787376faee5ed8466cfad57c99855e4ce2f6aa
RUN git clone -q --depth 1 --branch "${XGRAMMAR_TAG}" --recurse-submodules "${XGRAMMAR_REPO}" /src/xgrammar \
 && test "$(git -C /src/xgrammar rev-parse HEAD)" = "${XGRAMMAR_COMMIT}" \
 && test "$(git -C /src/xgrammar describe --tags --exact-match)" = "${XGRAMMAR_TAG}"

ARG TF_REPO=https://github.com/ashhart/TensorFold.git
ARG TF_REF
WORKDIR /src/tensorfold
RUN test -n "${TF_REF}" || { echo "TF_REF build arg is required" >&2; exit 1; } \
 && git init -q . \
 && git remote add origin "${TF_REPO}" \
 && git fetch -q --depth 1 origin "${TF_REF}" \
 && git checkout -q --detach FETCH_HEAD \
 && commit="$(git rev-parse HEAD)" \
 && if [[ "${TF_REF}" =~ ^[0-9a-f]{40}$ ]] && [[ "${commit}" != "${TF_REF}" ]]; then \
      echo "fetched ${commit}, expected ${TF_REF}" >&2; exit 1; fi \
 && echo "TensorFold ${TF_REPO} ${TF_REF} -> ${commit}"

# Only *.patch files apply (a .gitkeep or README may sit beside them); an empty set builds upstream as is.
COPY patches/engine/ /opt/tf-patches/engine/
RUN shopt -s nullglob \
 && patches=(/opt/tf-patches/engine/*.patch) \
 && for p in "${patches[@]}"; do \
      echo "apply ${p##*/}"; git apply --check "${p}" && git apply "${p}" || exit 1; \
    done \
 && echo "${#patches[@]} engine patch(es) applied" \
 && git status --short | sed -n '1,50p'

ARG ZIG_VERSION=0.17.0
ARG SM=121
ARG ZIG_CPU
ARG ZIG_JOBS=
ARG STRIP=false
# TOOLS=1 adds `zig build install`: the CUDA dev CLI (bin/tensorfold: run, digest, kl, ...), tf-cuda-test (GPU kernel
# suites, including `roce-gather`, which checks this pair's RoCE all-gather against NCCL's), tf-tp-test and
# tf-tp-watch-check. It costs ~4 more minutes (tf-cuda-test is the longest Zig compile).
ARG TOOLS=1
ARG PREFIX=/opt/tensorfold
# Every build compiles all kernels and executables again: upstream's build runs `nvcc --version` as a side-effect step
# whose output feeds every fatbin's cache key, so Zig's cache never hits across invocations. Only Zig's global cache
# (build runner, compiler_rt, libc++) is kept between builds, in a BuildKit cache mount.
RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig \
    test "$(cat .zig-version)" = "${ZIG_VERSION}" \
      || { echo "TensorFold needs Zig $(cat .zig-version), ZIG_VERSION=${ZIG_VERSION}" >&2; exit 1; } \
 && steps="native grammar" && if [[ "${TOOLS}" == 1 ]]; then steps="install ${steps}"; fi \
 && start=${SECONDS} \
 && zig build ${steps} --prefix "${PREFIX}" --summary failures \
      ${ZIG_JOBS:+-j${ZIG_JOBS}} -Doptimize=fast -Dcpu="${ZIG_CPU}" -Dstrip="${STRIP}" \
      -Dnvcc=/usr/local/cuda/bin/nvcc -Dsm="${SM}" -Dxgrammar=/src/xgrammar \
 && echo "zig build ${steps}: $((SECONDS - start)) s" \
 && rm -f "${PREFIX}"/bin/tf-qwen35-* \
 && test -x "${PREFIX}/native/bin/tensorfold-native"

COPY --from=codecs /codecs/lib/ ${PREFIX}/native/lib/
COPY --from=codecs /codecs/LICENSES/ ${PREFIX}/LICENSES/
RUN shopt -s nullglob \
 && mkdir -p "${PREFIX}/LICENSES/tensorfold" \
 && cp -r LICENSE NOTICE THIRD_PARTY_NOTICES.md LICENSES "${PREFIX}/LICENSES/tensorfold/" \
 && cp /src/xgrammar/LICENSE "${PREFIX}/LICENSES/xgrammar-Apache-2.0.txt" \
 && cp /src/xgrammar/NOTICE "${PREFIX}/LICENSES/xgrammar-NOTICE.txt" \
 && cp /src/xgrammar/3rdparty/dlpack/LICENSE "${PREFIX}/LICENSES/dlpack-Apache-2.0.txt" \
 && patches=(/opt/tf-patches/engine/*.patch) \
 && { echo "tf_repo=${TF_REPO}"; echo "tf_ref=${TF_REF}"; echo "tf_commit=$(git rev-parse HEAD)"; \
      echo "tf_version=$(sed -n 's/^[[:space:]]*\.version = "\([^"]*\)".*/\1/p' build.zig.zon)"; \
      echo "zig=${ZIG_VERSION} cpu=${ZIG_CPU} sm=${SM} optimize=fast strip=${STRIP}"; \
      echo "arch=$(uname -m)"; \
      echo "nvcc=$(nvcc --version | grep -o 'V[0-9][0-9.]*' | head -1)"; echo "xgrammar=${XGRAMMAR_TAG} ${XGRAMMAR_COMMIT}"; \
      echo "patches=${#patches[@]}"; for p in "${patches[@]}"; do sha256sum "${p}"; done; \
    } > "${PREFIX}/BUILD-INFO" \
 && cat "${PREFIX}/BUILD-INFO"

# Compile a portable video ABI against the pinned static codec libraries. The serving engine keeps its Spark CPU target.
FROM build AS video
ARG PREFIX=/opt/tensorfold
ARG ZIG_CPU
COPY --from=ffmpeg /ffmpeg/ /ffmpeg/
RUN --mount=type=cache,id=tensorfold-zig-global,target=/root/.cache/zig \
    zig build video --prefix "${PREFIX}" -Dffmpeg=/ffmpeg -Dcpu=generic --summary failures \
 && test -s "${PREFIX}/native/lib/libtfvideo.so" \
 && cp -r /ffmpeg/LICENSES "${PREFIX}/LICENSES/FFmpeg" \
 && cp /ffmpeg/BUILD-INFO "${PREFIX}/FFMPEG-BUILD-INFO"

# ---------------------------------------------------------------------------------------------------------------------
# NCCL: the CUDA runtime image carries libnccl.so.2, and the engine needs 2.30.0 or newer. When the base image's copy
# is older (or missing), NCCL_WHEEL=<version> installs that nvidia-nccl-cu13 wheel's library instead.
FROM toolchain AS nccl
ARG NCCL_WHEEL=
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN mkdir -p /opt/nccl \
 && if [[ -n "${NCCL_WHEEL}" ]]; then \
      apt-get update && apt-get install -y --no-install-recommends python3-pip python3-venv \
      && rm -rf /var/lib/apt/lists/* \
      && python3 -m venv /tmp/nv \
      && /tmp/nv/bin/pip install -q --no-cache-dir "nvidia-nccl-cu13==${NCCL_WHEEL}" \
      && cp -a "$(find /tmp/nv -name 'libnccl.so.2*' -type f | head -1)" /opt/nccl/libnccl.so.2 \
      && ls -l /opt/nccl; \
    fi

# ---------------------------------------------------------------------------------------------------------------------
# Runtime: the CUDA runtime image already carries everything the server dlopens besides the driver:
# libcublasLt.so.13 / libcublas.so.13 (cuBLAS 13.6) and libnccl.so.2. libcuda.so.1 and libnvidia-ml.so.1 come from the
# host driver through the NVIDIA container runtime (--gpus). The native engine uses no cuDNN.
# rdma-core adds libibverbs plus the mlx5 provider, which the engine's RoCE all-gather dlopens; without them (or
# without --device /dev/infiniband) every gather simply stays on NCCL.
FROM ${RUNTIME_IMAGE} AS runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
ARG PREFIX=/opt/tensorfold
ARG TF_REPO
ARG TF_REF
ARG SM=121
ARG ZIG_VERSION=0.17.0
ARG PATCHES_HASH=dev
ARG NCCL_MIN=2.30.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends libibverbs1 ibverbs-providers rdma-core python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=nccl /opt/nccl/ /opt/nccl/
COPY --from=video ${PREFIX}/ ${PREFIX}/
COPY patches/engine/HOME-LAB-NOTICE.md ${PREFIX}/HOME-LAB-NOTICE.md
# A wheel's libnccl replaces the base image's; then check that whatever is installed meets the engine's minimum.
RUN shopt -s nullglob \
 && if [[ -f /opt/nccl/libnccl.so.2 ]]; then \
      existing=$(find /usr/lib /lib -name 'libnccl.so.2' | head -1) \
      && if [[ -n "${existing}" ]]; then dir=$(dirname "${existing}"); else dir=/usr/lib/$(uname -m)-linux-gnu; fi \
      && mkdir -p "${dir}" \
      && rm -f "${dir}"/libnccl.so.2 "${dir}"/libnccl.so.2.[0-9]*.[0-9]* \
      && cp -a /opt/nccl/libnccl.so.2 "${dir}/libnccl.so.2" \
      && echo "NCCL replaced from the wheel in ${dir}"; \
    fi \
 && rm -rf /opt/nccl \
 && so=$(find /usr/lib /lib -name 'libnccl.so.2*' | head -1) \
 && test -n "${so}" || { echo "no libnccl.so.2 in the runtime image: pass --build-arg NCCL_WHEEL=<version>" >&2; exit 1; } \
 && ver=$( { grep -aoE '[0-9]+\.[0-9]+\.[0-9]+' "${so}" || true; } | { grep -xE '2\.[0-9]+\.[0-9]+' || true; } \
           | sort -u -t. -k1,1n -k2,2n -k3,3n | tail -1 ) \
 && echo "libnccl: ${so} (${ver:-unknown}), engine minimum ${NCCL_MIN}" \
 && if [ -n "${ver}" ]; then \
      printf '%s\n%s\n' "${NCCL_MIN}" "${ver}" | sort -C -t. -k1,1n -k2,2n -k3,3n \
        || { echo "libnccl ${ver} is older than ${NCCL_MIN}: pass --build-arg NCCL_WHEEL=<version>" >&2; exit 1; }; \
    else \
      echo "libnccl: could not read a version string; the GLM path needs ${NCCL_MIN} or newer (it does not check at run time)" >&2; \
    fi
# `tensorfold` is the server too, as upstream's packaging links it (its docs run `tensorfold serve`); the CUDA dev CLI
# that a source install names bin/tensorfold is linked as `tensorfold-cuda`.
RUN shopt -s nullglob \
 && ln -s "${PREFIX}/native/bin/tensorfold-native" /usr/local/bin/tensorfold-native \
 && ln -s "${PREFIX}/native/bin/tensorfold-native" /usr/local/bin/tensorfold \
 && for exe in "${PREFIX}"/bin/*; do \
      name="${exe##*/}"; [[ "${name}" == tensorfold ]] && name=tensorfold-cuda; ln -s "${exe}" "/usr/local/bin/${name}"; \
    done \
 && test -x "${PREFIX}/native/bin/tensorfold-native"
ARG SITE_PATCHES_HASH
LABEL tf.site_patches="${SITE_PATCHES_HASH}" \
      tf.patches="${PATCHES_HASH}" \
      tf.engine="zig" \
      tf.ref="${TF_REF}" \
      tf.repo="${TF_REPO}" \
      tf.sm="${SM}" \
      tf.zig="${ZIG_VERSION}"
WORKDIR /workspace
CMD ["tensorfold-native", "--help"]
