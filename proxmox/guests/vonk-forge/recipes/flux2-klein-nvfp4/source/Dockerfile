FROM nvcr.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04@sha256:36050649ad1acc5d3de2c26620191c25850fb12a5771b6c22996033003d952e4

ARG DEBIAN_FRONTEND=noninteractive
ARG COMFYUI_REVISION=12d5279438bfefc058a269eae805ceab6047777f
ARG COMFYUI_ARCHIVE_SHA256=6d8ab87ec1250e60101f0caf9e11658834c29d9cd76c9174e2b84ec9436f4886

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://codeload.github.com/comfyanonymous/ComfyUI/tar.gz/${COMFYUI_REVISION}" -o /tmp/comfyui.tar.gz \
    && echo "${COMFYUI_ARCHIVE_SHA256}  /tmp/comfyui.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/comfyui \
    && tar -xzf /tmp/comfyui.tar.gz --strip-components=1 -C /opt/comfyui \
    && rm /tmp/comfyui.tar.gz

RUN python3 -m pip install --break-system-packages --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu130 \
      torch==2.13.0 torchvision==0.28.0 torchaudio==2.11.0 \
    && python3 -m pip install --break-system-packages --no-cache-dir -r /opt/comfyui/requirements.txt

# Retain checks inside the image because the native build API discards stdout.
COPY build_checks.py /opt/vonk/source/build_checks.py
RUN python3 /opt/vonk/source/build_checks.py

RUN groupadd --gid 10001 vonk \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin vonk \
    && mkdir -p /opt/vonk/bin /opt/vonk/source/workflows /outputs \
    && chown -R 10001:10001 /opt/vonk /outputs

COPY --chown=10001:10001 comfyui_job.py /opt/vonk/bin/comfyui-job
COPY --chown=10001:10001 workflows/ /opt/vonk/source/workflows/

RUN chmod 0555 /opt/vonk/bin/comfyui-job

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

USER 10001:10001
WORKDIR /opt/comfyui
ENTRYPOINT []
