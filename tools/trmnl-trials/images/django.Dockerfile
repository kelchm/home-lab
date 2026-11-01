FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f
RUN apt-get update && apt-get install -y --no-install-recommends imagemagick && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY .trial/django-requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=byos_django.settings
RUN playwright install --with-deps firefox
COPY . .
COPY .trial/django_asgi.py /app/trial_asgi.py
COPY .trial/django-entrypoint.sh /usr/local/bin/trial-entrypoint
RUN python manage.py collectstatic --noinput && useradd --uid 1000 --create-home app && mkdir -p /data && chown -R app:app /app /data && chmod +x /usr/local/bin/trial-entrypoint
USER 1000:1000
EXPOSE 8000
ENTRYPOINT ["trial-entrypoint"]
