from django.db import migrations


OLD_TASK_NAME = 'storage: cleanup_expired_objects'
NEW_TASK_NAME = 'exchange: cleanup_expired_files'
NEW_TASK_PATH = 'exchange.tasks.cleanup_expired_exchange_files'


def replace_task(apps, schema_editor):
    """storage.tasks.cleanup_expired_objects удалена: она вызывала detach()
    для FileObject, на которые ещё ссылались живые записи потребителей, и
    поэтому не удаляла ничего, но отчитывалась об успехе. Очистка по TTL
    переехала в сами модули-потребители (пока только обменник)."""
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    PeriodicTask.objects.filter(name=OLD_TASK_NAME).delete()

    daily_at_3am, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='3', day_of_week='*', day_of_month='*', month_of_year='*',
    )
    PeriodicTask.objects.get_or_create(
        name=NEW_TASK_NAME,
        defaults={'task': NEW_TASK_PATH, 'crontab': daily_at_3am, 'enabled': True},
    )


def restore_task(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(name=NEW_TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0002_seed_periodic_tasks'),
        ('django_celery_beat', '0019_alter_periodictasks_options'),
    ]

    operations = [
        migrations.RunPython(replace_task, restore_task),
    ]
