import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'social_network.settings')

app = Celery('social_network')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
