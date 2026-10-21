# tensorfold-native alone, from the kit's pinned engine and patches plus this mod's server patches.
# The context is build.sh's: patches/ (this mod's), fatbin/ (the published image's own CUDA fatbins) and its binary.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
# gcc and libc6-dev give the system C runtime the kit's build links; libibverbs-dev's header turns the RoCE proxy on.
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends binutils ca-certificates curl gcc git libc6-dev libibverbs-dev xz-utils >/dev/null
ARG ZIG_VERSION
ARG ZIG_SHA256
RUN curl -fsSL -o /tmp/zig.tar.xz "https://ziglang.org/download/${ZIG_VERSION}/zig-aarch64-linux-${ZIG_VERSION}.tar.xz" && \
    echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz
ARG ENGINE_REPOSITORY
ARG ENGINE_REVISION
RUN git init -q /tensorfold && cd /tensorfold && \
    git remote add origin "https://github.com/${ENGINE_REPOSITORY}.git" && \
    git fetch -q --depth 1 origin "${ENGINE_REVISION}" && git checkout -q --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${ENGINE_REVISION}"
ARG KIT_REPOSITORY
ARG KIT_REVISION
ARG KIT_PATCHES_SHA256
RUN git init -q /kit && cd /kit && git remote add origin "https://github.com/${KIT_REPOSITORY}.git" && \
    git fetch -q --depth 1 origin "${KIT_REVISION}" && git checkout -q --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${KIT_REVISION}" && \
    test "$(cat patches/*.patch | sha256sum | cut -d' ' -f1)" = "${KIT_PATCHES_SHA256}"
RUN cd /tensorfold && for p in /kit/patches/*.patch; do \
      echo "applying $p"; git apply --check "$p" && git apply "$p" || exit 1; done
COPY fatbin /opt/fatbin
COPY published /opt/published
ARG ZIG_CPU
# The kit's tree alone must reproduce every loaded section of the published binary before the site patches go on.
RUN cd /tensorfold && /opt/zig/zig build -Dfatbins=/opt/fatbin -Doptimize=fast "-Dcpu=${ZIG_CPU}" \
      --prefix /opt/reference -j"$(nproc)" native && \
    for s in .text .rodata .data .eh_frame; do \
      a=$(objcopy -O binary --only-section=$s /opt/published /dev/stdout | sha256sum) && \
      b=$(objcopy -O binary --only-section=$s /opt/reference/native/bin/tensorfold-native /dev/stdout | sha256sum) && \
      test "$a" = "$b" || { echo "$s differs from the published binary"; exit 1; }; done
COPY patches /opt/site-patches
RUN cd /tensorfold && for p in /opt/site-patches/*.patch; do \
      echo "applying $p"; git apply --check "$p" && git apply "$p" || exit 1; done
RUN cd /tensorfold && /opt/zig/zig build -Dfatbins=/opt/fatbin -Doptimize=fast "-Dcpu=${ZIG_CPU}" \
      --prefix /opt/tensorfold -j"$(nproc)" native && \
    test -x /opt/tensorfold/native/bin/tensorfold-native

FROM scratch
COPY --from=build /opt/tensorfold/native/bin/tensorfold-native /tensorfold-native
