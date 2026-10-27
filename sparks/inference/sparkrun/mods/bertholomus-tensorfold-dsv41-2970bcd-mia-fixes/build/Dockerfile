# DeepSeek-V4.1 TensorFold TP2: the deepseek-v41-zig native server and rank-1 lanes, and the deepseek-v41-tp2 Python
# engine that writes the rank caches and token map, on NVIDIA PyTorch 26.07. build.sh supplies tf-zig and tf-py.
FROM nvcr.io/nvidia/pytorch@sha256:2140e699b3beaf7f96a0081fd9c9406bc3832b435cdb60dfa2d261f7d2f34a1c
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl xz-utils libibverbs-dev >/dev/null && \
    rm -rf /var/lib/apt/lists/*
ARG ZIG_SHA256=9e8d11661d4ae3bd57702a3832781e23ad151dde5798e16a5ccd503f65234ff8
RUN curl -fsSL -o /tmp/zig.tar.xz https://ziglang.org/download/0.17.0/zig-aarch64-linux-0.17.0.tar.xz && \
    echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz
COPY tf-zig /opt/tf-zig
RUN cd /opt/tf-zig && /opt/zig/zig build install native -Dtarget=aarch64-linux-gnu -Doptimize=ReleaseSafe \
      -Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=121 --prefix /opt/tf-native -j"$(nproc)" && \
    ls -la /opt/tf-native/native/bin /opt/tf-native/bin
COPY tf-py /opt/tf-py
RUN pip install -q -e /opt/tf-py
ENV PATH=/opt/tf-native/native/bin:/opt/tf-native/bin:$PATH
