FROM ghcr.io/miaai-lab/glm-5.3-flash-2x-dgx-sparks@sha256:9bb1557a4234fce63d59599e44d10747eabd742beb337eebf9e7070be8a0fd58

LABEL org.opencontainers.image.source="https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks" \
      org.opencontainers.image.revision="493cb88fc69f8ba73ac87404f429d763e2739d89" \
      org.opencontainers.image.licenses="MIT"

# The published image already contains MiaAI-Lab's compiled SM121 EXL3 and
# DFlash2 overlays. Apply every source-exact launch-time patch while the image
# is still writable so the Controller can run the final runtime as an
# unprivileged, offline container without SSH or host-side mutation.
COPY patch_kpool_tail_slotmap.py test_kpool_tail_slotmap.py /opt/glm53/

RUN python3 /opt/glm53/patch_kpool_tail_slotmap.py \
    && python3 /opt/glm53/patch_glm_video_placeholders.py \
    && python3 /opt/glm53/patch_suppress_stops_in_reasoning.py \
    && python3 /opt/glm53/patch_scheduler_decode_floor.py \
    && python3 /opt/glm53/patch_glm5_drafter_group.py \
    && python3 /opt/glm53/patch_hybrid_prefix_hit.py \
    && python3 /opt/glm53/patch_xgrammar_termination.py \
    && EXL3_SELFCHECK_GPU=0 python3 /opt/glm53/test_exl3_overlay.py \
    && python3 /opt/glm53/test_suppress_stops.py \
    && python3 /opt/glm53/test_scheduler_decode_floor.py \
    && python3 /opt/glm53/test_hybrid_prefix_hit.py \
    && python3 /opt/glm53/test_xgrammar_termination.py \
    && python3 /opt/glm53/test_kpool_tail_slotmap.py

COPY --chmod=0755 vllm-wrapper.py /opt/vonk/bin/vllm
COPY verify-runtime.py /opt/vonk/verify-runtime.py

RUN python3 /opt/vonk/verify-runtime.py \
    && if ! getent group 10001 >/dev/null; then groupadd --gid 10001 vonk; fi \
    && if ! getent passwd 10001 >/dev/null; then useradd --uid 10001 --gid 10001 --home-dir /state --no-create-home --shell /usr/sbin/nologin vonk; fi \
    && install --directory --owner 10001 --group 10001 /state /outputs /outputs/cache /outputs/cache/triton /outputs/cache/tilelang

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1 \
    VLLM_ENGINE_READY_TIMEOUT_S=3600 \
    VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=1800 \
    VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=1 \
    GLM53_SUPPRESS_STOPS_IN_REASONING=1 \
    GLM53_MIXED_PREFILL_CHUNK=skip \
    EXL3_FUSED_MOE=1 \
    EXL3_MOE_ROW_TILE=0 \
    EXL3_TEMP_ROWS_FUSED=128 \
    TORCH_CUDA_ARCH_LIST=12.1a \
    FLASHINFER_CUDA_ARCH_LIST=12.1a \
    FLASHINFER_DISABLE_VERSION_CHECK=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    HOME=/state \
    XDG_CACHE_HOME=/outputs/cache \
    VLLM_CACHE_ROOT=/outputs/cache \
    TRITON_CACHE_DIR=/outputs/cache/triton \
    TILELANG_CACHE_DIR=/outputs/cache/tilelang

WORKDIR /state
USER 10001:10001
ENTRYPOINT []
