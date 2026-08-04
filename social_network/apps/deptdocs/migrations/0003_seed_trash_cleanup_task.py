from django.db import migrations


TASK_NAME = 'deptdocs: cleanup_trash'
TASK_PATH = 'deptdocs.tasks.cleanup_deptdocs_trash'


def create_periodic_task(apps, schema_editor):
    """Автоочистка корзины документов отдела по расписанию — см.
    catalog/migrations/0002_seed_trash_cleanup_task.py, тот же принцип."""
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
        ('deptdocs', '0002_remove_departmentdocument_allowed_departments_and_more'),
        ('django_celery_beat', '0019_alter_periodictasks_options'),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, remove_periodic_task),
    ]
