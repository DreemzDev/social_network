"""Дедупликация — то, ради чего storage вообще устроен как две модели.

Свойство проверялось только косвенно (в тестах copy_reference и каскадов),
хотя оно и есть главное обещание модуля: одно содержимое — одна копия на
диске, независимо от того, сколько раз и под какими именами его загрузили.

Отдельно проверяется, что дедупликация именно ФИЗИЧЕСКАЯ: совпадение
записей в БД ничего не стоит, если на диск при этом легли два файла.
"""

import os
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from catalog.models import CatalogDocument
from exchange.models import ExchangeFile
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()

CONTENT = b'texto identico para deduplicacion' * 10


def blobs_dir():
    return os.path.join(str(settings.MEDIA_ROOT), 'storage', 'blobs')


def files_on_disk():
    root = blobs_dir()
    if not os.path.isdir(root):
        return []
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        found.extend(filenames)
    return found


class DeduplicationTest(TestCase):
    """Каждому тесту — свой MEDIA_ROOT.

    Без этого тесты, проверяющие состояние диска, недостоверны: TestCase
    откатывает БД между тестами, но файловую систему не трогает. Файл,
    записанный предыдущим тестом, остаётся, а запись FileBlob о нём
    исчезает — и следующая загрузка того же содержимого считает blob
    новым, пишет файл повторно и получает от FileSystemStorage имя с
    суффиксом (<checksum>_Wjsmoah). Проверка «на диске один файл» при этом
    падает, хотя сам код дедупликации исправен.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='dedup-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='dedup_user', password='pass12345')
        self.other = User.objects.create_user(username='dedup_other', password='pass12345')

    def _upload(self, name, *, user=None, content=CONTENT, category=None):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(
            uploaded,
            user=user or self.user,
            category=category or FileObject.Category.EXCHANGE,
        )

    def test_same_content_twice_creates_one_blob(self):
        first = self._upload('первый.pdf')
        second = self._upload('второй.pdf')

        self.assertNotEqual(first.pk, second.pk, 'FileObject должны быть разными')
        self.assertEqual(first.blob_id, second.blob_id, 'blob должен быть общим')
        self.assertEqual(FileBlob.objects.count(), 1)

    def test_same_content_twice_writes_one_file_to_disk(self):
        """Главная проверка: совпадение blob_id в БД ничего не значит, если
        на диск всё равно лёг второй файл."""
        before = len(files_on_disk())

        self._upload('первый.pdf')
        after_first = len(files_on_disk())

        self._upload('второй.pdf')
        after_second = len(files_on_disk())

        self.assertEqual(after_first, before + 1, 'первая загрузка должна создать один файл')
        self.assertEqual(after_second, after_first, 'вторая загрузка не должна писать на диск')

    def test_file_on_disk_is_named_by_checksum(self):
        """Имя файла определяется только содержимым — поэтому повторная
        запись того же содержимого и попадает в тот же путь. Если бы имя
        зависело от оригинального названия, дедупликация была бы
        невозможна в принципе."""
        file_object = self._upload('какое-угодно-имя.pdf')
        blob = file_object.blob

        self.assertEqual(os.path.basename(blob.file.name), blob.checksum)
        self.assertEqual(len(blob.checksum), 64)

    def test_different_users_share_one_blob(self):
        """Документ, разосланный всем сотрудникам, физически хранится один
        раз — это и есть основная экономия (ARCHITECTURE.md, раздел 9)."""
        mine = self._upload('мой.pdf', user=self.user)
        theirs = self._upload('его.pdf', user=self.other)

        self.assertEqual(mine.blob_id, theirs.blob_id)
        self.assertEqual(len(files_on_disk()), 1)

    def test_different_categories_share_one_blob(self):
        """Один и тот же файл в обменнике и в каталоге — один blob и две
        именованные ссылки с разными сроками хранения."""
        in_exchange = self._upload('в обменнике.pdf', category=FileObject.Category.EXCHANGE)
        in_catalog = self._upload('в каталоге.pdf', category=FileObject.Category.CATALOG)

        self.assertEqual(in_exchange.blob_id, in_catalog.blob_id)
        self.assertEqual(FileBlob.objects.count(), 1)
        self.assertNotEqual(in_exchange.category, in_catalog.category)

    def test_different_content_does_not_share_blob(self):
        """Обратная проверка: дедупликация не должна склеивать разное."""
        first = self._upload('а.pdf', content=b'aaa')
        second = self._upload('б.pdf', content=b'bbb')

        self.assertNotEqual(first.blob_id, second.blob_id)
        self.assertEqual(FileBlob.objects.count(), 2)
        self.assertEqual(len(files_on_disk()), 2)

    def test_quota_counts_shared_blob_once(self):
        """get_usage() считает по уникальным blob'ам: иначе один и тот же
        файл, использованный трижды, съедал бы тройную квоту, хотя место
        занимает одно."""
        self._upload('раз.pdf')
        self._upload('два.pdf')
        self._upload('три.pdf')

        usage = StorageService.get_usage(self.user)

        self.assertEqual(usage, len(CONTENT))

    def test_deleting_one_reference_keeps_file_for_the_other(self):
        """Ключевое следствие дедупликации: удаление одного использования
        не должно утаскивать за собой чужое."""
        shared = self._upload('общий.pdf')
        copy = StorageService.copy_reference(
            shared, user=self.user, category=FileObject.Category.CATALOG,
        )

        exchange_file = ExchangeFile.objects.create(
            file_object=shared, owner=self.user, uploaded_by=self.user,
        )
        CatalogDocument.objects.create(
            file_object=copy, title='Копия', uploaded_by=self.user,
        )

        exchange_file.delete()  # detach() выполнит сигнал post_delete

        blob = FileBlob.objects.get(pk=copy.blob_id)
        self.assertEqual(blob.status, FileBlob.Status.ACTIVE, 'blob нужен второй ссылке')
        self.assertTrue(os.path.exists(blob.file.path), 'файл не должен исчезнуть с диска')
        self.assertTrue(FileObject.objects.filter(pk=copy.pk).exists())


class TestMediaIsolationTest(TestCase):
    """Тесты обязаны писать во временный каталог, а не в боевой media/.

    Django создаёт и удаляет тестовую БД, но файлы за собой не убирает:
    без изоляции каждый прогон оставлял blob'ы в media/storage/blobs/
    навсегда. К моменту, когда это заметили, там лежало 2862 файла при 25
    записях в БД, причём в сотнях копий одного содержимого — на втором
    прогоне FileSystemStorage находил файл существующим и дописывал
    случайный суффикс.
    """

    def test_media_root_is_not_the_project_media_dir(self):
        project_media = os.path.join(str(settings.BASE_DIR), 'media')

        self.assertNotEqual(
            os.path.normcase(os.path.abspath(str(settings.MEDIA_ROOT))),
            os.path.normcase(os.path.abspath(project_media)),
            'MEDIA_ROOT под тестами должен указывать во временный каталог '
            '(см. settings.py, ветка "test" in sys.argv)',
        )
