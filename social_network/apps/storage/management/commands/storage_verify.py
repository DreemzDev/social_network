from django.core.management.base import BaseCommand

from storage.services import StorageService


class Command(BaseCommand):
    help = (
        'Сверяет файлы на диске (media/storage/blobs) с записями FileBlob в БД. '
        'По умолчанию только выводит отчёт, ничего не удаляет.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete-untracked', action='store_true',
            help='Удалить с диска файлы, для которых нет записи в БД (необратимо).',
        )

    def handle(self, *args, **options):
        untracked = StorageService.find_untracked_files()

        if not untracked:
            self.stdout.write(self.style.SUCCESS('Файлов без записи в БД не найдено.'))
            return

        self.stdout.write(self.style.WARNING(f'Найдено файлов без записи в БД: {len(untracked)}'))
        for path in untracked:
            self.stdout.write(f'  {path}')

        if options['delete_untracked']:
            import os
            for path in untracked:
                os.remove(path)
            self.stdout.write(self.style.SUCCESS(f'Удалено файлов: {len(untracked)}'))
        else:
            self.stdout.write('Запустите с --delete-untracked, чтобы удалить их физически.')
