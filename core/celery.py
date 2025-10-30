"""Celery application setup for Django project."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.prod")

app = Celery("tutreklama")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@app.task(bind=True)
def healthcheck(self) -> str:
    return "ok"
