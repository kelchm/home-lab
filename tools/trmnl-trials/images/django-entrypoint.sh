#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py shell -c 'import os; from django.contrib.auth import get_user_model; user, created = get_user_model().objects.get_or_create(username=os.environ["DJANGO_SUPERUSER_USERNAME"], defaults={"is_staff": True, "is_superuser": True, "email": os.environ["DJANGO_SUPERUSER_EMAIL"]}); user.set_password(os.environ["DJANGO_SUPERUSER_PASSWORD"]) if created else None; user.save() if created else None'
exec daphne -b 0.0.0.0 -p 8000 trial_asgi:application
