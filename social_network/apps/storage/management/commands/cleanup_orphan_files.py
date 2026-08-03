from django.core.management.base import BaseCommand

from storage.services import StorageService


class Command(BaseCommand):
    help = (
        'Физически удаляет blob-файлы, пробывшие в статусе ORPHAN дольше '
        'STORAGE_ORPHAN_RETENTION_DAYS. Запускать ежедневно через cron/Task Scheduler.'
    )

    def handle(self, *args, **options):
        purged = StorageService.purge_expired_orphans()
        self.stdout.write(self.style.SUCCESS(f'Удалено файлов: {purged}'))
