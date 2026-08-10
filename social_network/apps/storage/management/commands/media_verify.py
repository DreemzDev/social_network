import os

from django.conf import settings
from django.core.management.base import BaseCommand

from storage.mediafiles import find_untracked_media


class Command(BaseCommand):
    help = (
        'Сверяет файлы картинок в MEDIA_ROOT (аватары, обложки, галерея, '
        'изображения постов, логотип) с записями в БД. По умолчанию только '
        'выводит отчёт, ничего не удаляет. Каталог storage/ не трогает — '
        'у него своя команда storage_verify.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete-untracked', action='store_true',
            help='Удалить с диска файлы, на которые нет ссылки из БД (необратимо).',
        )

    def handle(self, *args, **options):
        untracked = find_untracked_media()

        if not untracked:
            self.stdout.write(self.style.SUCCESS('Файлов без записи в БД не найдено.'))
            return

        total = sum(os.path.getsize(path) for path in untracked)
        self.stdout.write(self.style.WARNING(
            f'Найдено файлов без записи в БД: {len(untracked)} '
            f'({total / 1024 / 1024:.1f} МБ)'
        ))
        for path in untracked:
            self.stdout.write(f'  {os.path.relpath(path, settings.MEDIA_ROOT)}')

        if not options['delete_untracked']:
            self.stdout.write('Запустите с --delete-untracked, чтобы удалить их физически.')
            return

        deleted = 0
        for path in untracked:
            try:
                os.remove(path)
                deleted += 1
            except OSError as error:
                self.stderr.write(f'  не удалось удалить {path}: {error}')

        self.stdout.write(self.style.SUCCESS(f'Удалено файлов: {deleted}'))
