from django.db import migrations


TASK_NAME = 'catalog: cleanup_trash'
TASK_PATH = 'catalog.tasks.cleanup_catalog_trash'


def create_periodic_task(apps, schema_editor):
    """Автоочистка корзины каталога по расписанию — до этой задачи документ,
    отправленный в корзину, оставался там навсегда без ручного удаления
    (см. storage/migrations/0002_seed_periodic_tasks.py — тот же принцип:
    расписание в БД, а не в конфиге ОС, чтобы Windows-разработка и
    Astra Linux-прод не расходились в способе запуска)."""
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    daily_at_3am, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='3', day_of_week='*', day_of_month='*', month_of_year='*',
    )
    PeriodicTask.objects.get_or_create(
        name=TASK_NAME, defaults={'task': TASK_PATH, 'crontab': daily_at_3am, 'enabled': True},
    )


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
        ('django_celery_beat', '0019_alter_periodictasks_options'),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, remove_periodic_task),
    ]
