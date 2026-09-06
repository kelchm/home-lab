FROM ghcr.io/aeon-7/comfyui-aeon-spark@sha256:7fda74d7af1d86455bfa58df5d36e761964017c7bce6f5d2f3564ba0b2deee3a

# The parent's entrypoint updates packages and writes its installation tree.
# This variant executes only the immutable Vonk adapter with the baked venv.
ENTRYPOINT []
USER 0
LABEL ai.vonkforge.runtime-interface="v1" \
      org.opencontainers.image.source="https://github.com/AEON-7/comfyui-aeon-spark" \
      io.kelchm.comfyui.revision="fce0398470fe3ecdb7ab4c5c69555ad0fcbdc09e"

ENV PATH=/opt/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PIP_NO_INDEX=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TORCH_COMPILE_DISABLE=1 \
    TORCHDYNAMO_DISABLE=1 \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8 \
    MAX_JOBS=8 \
    CMAKE_BUILD_PARALLEL_LEVEL=8 \
    NINJAFLAGS=-j8 \
    MAKEFLAGS=-j8

COPY . /opt/vonk/source/
RUN test -x /opt/venv/bin/python3 \
    && /opt/venv/bin/python3 /opt/vonk/source/build_checks.py \
    && if ! getent group 10001 >/dev/null; then groupadd --gid 10001 vonk; fi \
    && if ! getent passwd 10001 >/dev/null; then useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin vonk; fi \
    && install --directory --owner 10001 --group 10001 /outputs \
    && install --directory /opt/vonk/bin \
    && install --mode 0555 /opt/vonk/source/comfyui_job.py /opt/vonk/bin/comfyui-job

USER 10001:10001
WORKDIR /opt/ComfyUI
