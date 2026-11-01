FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f
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
