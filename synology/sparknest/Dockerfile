# Athena deployment: upstream binaries plus an opt-in HF helper patch.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fuse3 libibverbs1 patch python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --retry 3 \
        https://github.com/tpurtell/sparknest/releases/download/v0.8.3/sparknest-0.8.3-linux-x86_64.tar.gz \
        --output /tmp/sparknest.tar.gz \
    && echo 'ea58438cf4c03e948f1b05b0b926c2daeb20c7de2208f246ac414a8d66a1400b  /tmp/sparknest.tar.gz' | sha256sum --check \
    && tar --extract --gzip --file /tmp/sparknest.tar.gz --directory /opt \
    && cp /opt/sparknest-0.8.3-linux-x86_64/bin/nest /opt/sparknest-0.8.3-linux-x86_64/bin/sparknestd /usr/local/bin/ \
    && mkdir -p /usr/local/libexec/sparknest /mnt/sparknest \
    && cp /opt/sparknest-0.8.3-linux-x86_64/libexec/sparknest/sparknest-hf-fetch /usr/local/libexec/sparknest/ \
    && mkdir -p /usr/share/doc/sparknest \
    && cp /opt/sparknest-0.8.3-linux-x86_64/share/doc/sparknest/LICENSE-* /usr/share/doc/sparknest/ \
    && rm -rf /tmp/sparknest.tar.gz /opt/sparknest-0.8.3-linux-x86_64

RUN python3 -m venv /opt/hf \
    && /opt/hf/bin/pip install --no-cache-dir 'huggingface_hub==2.4.0' 'hf-xet==1.8.0'

COPY patches/0001-offline-share.patch /tmp/offline-share.patch
COPY tests/test_offline_share.py /tmp/test_offline_share.py
RUN patch --batch --fuzz=0 /usr/local/libexec/sparknest/sparknest-hf-fetch /tmp/offline-share.patch \
    && /opt/hf/bin/python3 /tmp/test_offline_share.py ChunkerGolden \
    && rm /tmp/offline-share.patch /tmp/test_offline_share.py

ENV PATH="/opt/hf/bin:${PATH}" \
    SPARKNEST_HF_FETCH=/usr/local/libexec/sparknest/sparknest-hf-fetch \
    HF_HOME=/mnt/sparknest/hf-home \
    HF_TOKEN_PATH=/tmp/hf-token \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL org.opencontainers.image.source="https://github.com/kelchm/home-lab" \
      org.opencontainers.image.version="0.8.3-2" \
      org.opencontainers.image.description="SparkNest v0.8.3 with opt-in offline HF sharing"

CMD ["sparknestd", "--config", "/config/node.toml"]
