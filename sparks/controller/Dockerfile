FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

# git: repo sync; openssh-client: ansible transport to both hosts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/spark-controller
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app

ENV PATH="/srv/spark-controller/.venv/bin:$PATH" \
    PYTHONPATH=/srv/spark-controller \
    PYTHONUNBUFFERED=1

# Loopback only: Caddy on :80 is the sole front door for /admin.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 127.0.0.1 --port ${SPARK_CONTROLLER_PORT:-9800}"]
