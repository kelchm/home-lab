FROM docker.io/vllm/vllm-openai@sha256:3b0e188ffceb3d07e09c3cb5215433a0020eacf02d7f882ed3a8bfd15454477e

LABEL org.opencontainers.image.source="https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks" \
      org.opencontainers.image.revision="c2325b22602b51a5faf55fc2bebccc34f3f80b9f" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later AND Apache-2.0" \
      ai.vonkforge.runtime-interface="v1"

COPY . /opt/vonk/source/
RUN python3 /opt/vonk/source/apply-patches.py \
    && python3 -c 'import os, runpy, torch, vllm; assert os.path.isfile("/usr/local/bin/vllm") and os.access("/usr/local/bin/vllm", os.X_OK); helper = runpy.run_path("/opt/vonk/source/upstream/files/patch_checkpoint_config.py"); assert all(callable(helper[name]) for name in ("main", "mtp_moe_algo")); print("VONK_RUNTIME_VERSIONS", torch.__version__, vllm.__version__)' \
    && if ! getent group 10001 >/dev/null; then groupadd --gid 10001 vonk; fi \
    && if ! getent passwd 10001 >/dev/null; then useradd --uid 10001 --gid 10001 --home-dir /state --no-create-home --shell /usr/sbin/nologin vonk; fi \
    && install --directory --owner 10001 --group 10001 /state /outputs /outputs/cache \
    && install --directory /opt/vonk/bin \
    && install --mode 0555 /opt/vonk/source/vllm-wrapper.py /opt/vonk/bin/vllm

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_PLE_CPU_OFFLOAD=0 \
    NCCL_IB_AUTO_DETECT=0 \
    HOME=/state \
    XDG_CACHE_HOME=/outputs/cache \
    VLLM_CACHE_ROOT=/outputs/cache/vllm \
    TRITON_CACHE_DIR=/outputs/cache/triton \
    TORCH_EXTENSIONS_DIR=/outputs/cache/torch_extensions

WORKDIR /state
USER 10001:10001
ENTRYPOINT []
