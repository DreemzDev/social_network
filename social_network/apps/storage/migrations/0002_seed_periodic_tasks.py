from django.db import migrations


DAILY_CLEANUP_TASKS = [
    ('storage: cleanup_expired_objects', 'storage.tasks.cleanup_expired_objects'),
    ('storage: cleanup_orphan_files', 'storage.tasks.cleanup_orphan_files'),
]

MONTHLY_VERIFY_TASK = ('storage: storage_verify', 'storage.tasks.storage_verify')


def create_periodic_tasks(apps, schema_editor):
    """Регистрирует расписание в БД через django_celery_beat, а не в коде —
    расписание правится через /admin/ на конкретном окружении без деплоя
    (ARCHITECTURE.md, раздел 7: ежедневная очистка, ежемесячная сверка)."""
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    daily_at_3am, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='3', day_of_week='*', day_of_month='*', month_of_year='*',
    )
    for name, task_path in DAILY_CLEANUP_TASKS:
        PeriodicTask.objects.get_or_create(
            name=name, defaults={'task': task_path, 'crontab': daily_at_3am, 'enabled': True},
        )

    monthly_1st_at_4am, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='4', day_of_week='*', day_of_month='1', month_of_year='*',
    )
    name, task_path = MONTHLY_VERIFY_TASK
    PeriodicTask.objects.get_or_create(
        name=name, defaults={'task': task_path, 'crontab': monthly_1st_at_4am, 'enabled': True},
    )


def remove_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    names = [name for name, _ in DAILY_CLEANUP_TASKS] + [MONTHLY_VERIFY_TASK[0]]
    PeriodicTask.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0001_initial'),
        ('django_celery_beat', '0019_alter_periodictasks_options'),
    ]

    operations = [
        migrations.RunPython(create_periodic_tasks, remove_periodic_tasks),
    ]
