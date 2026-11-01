FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY .trial/fastapi-requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . .
RUN useradd --uid 1000 --create-home app && mkdir -p var && chown -R app:app /app
ENV PYTHONUNBUFFERED=1 LOG_LEVEL=INFO
USER 1000:1000
EXPOSE 4567
CMD ["python", "-m", "trmnl_server"]
