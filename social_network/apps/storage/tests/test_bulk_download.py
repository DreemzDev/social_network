"""Массовое скачивание выбранных файлов одним zip.

Единственное место, где содержимое файлов идёт ЧЕРЕЗ процесс Django:
архива нет на диске, поэтому X-Accel-Redirect на него не сошлёшь
(ARCHITECTURE.md, раздел 8). Отсюда всё остальное в этом модуле — сборка
на лету вместо буфера в памяти, ZIP_STORED вместо сжатия и жёсткие
пределы на размер запроса.

Права, как обычно, проверяет модуль-потребитель, а не storage: у каталога
и обменника содержимое видно всем сотрудникам, у приватного доступа —
только участникам allowed_users. Проверяется это здесь же, потому что
именно тут сужение списка легко забыть: id перечислены в query-строке, и
без фильтра по правам архив выгрузил бы закрытые папки целиком.
"""

import io
import shutil
import tempfile
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import CatalogDocument
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


def read_archive(response) -> zipfile.ZipFile:
    """Собирает потоковый ответ и открывает его как zip.

    Тест обязан пройти по streaming_content, а не по .content: у
    StreamingHttpResponse последнего просто нет, и это не формальность —
    именно так проверяется, что архив собрался целиком, а не оборвался
    на первом чанке.
    """
    return zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))


class BulkDownloadArchiveTest(TestCase):
    """Свой MEDIA_ROOT на каждый тест — содержимое читается с диска."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='zip-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='zip_user', password='pass12345')
        self.client.force_login(self.user)

    def make_document(self, name, content):
        file_object = StorageService.upload(
            SimpleUploadedFile(name, content), user=self.user, category=FileObject.Category.CATALOG,
        )
        return CatalogDocument.objects.create(
            file_object=file_object, title=name, uploaded_by=self.user,
        )

    def download(self, documents, **params):
        params['ids'] = ','.join(str(document.pk) for document in documents)
        return self.client.get(reverse('catalog_bulk_download'), params)

    def test_archive_contains_every_selected_file_with_its_content(self):
        first = self.make_document('Приказ №1.pdf', b'PDF ONE')
        second = self.make_document('Смета.xlsx', b'XLSX TWO')

        archive = read_archive(self.download([first, second]))

        self.assertEqual(sorted(archive.namelist()), ['Приказ №1.pdf', 'Смета.xlsx'])
        self.assertEqual(archive.read('Приказ №1.pdf'), b'PDF ONE')
        self.assertEqual(archive.read('Смета.xlsx'), b'XLSX TWO')

    def test_archive_is_readable_although_built_on_an_unseekable_stream(self):
        """Главная техническая проверка. zipfile обычно возвращается назад и
        правит заголовок записи, когда узнал размер и CRC; в потоке так
        нельзя, и он обязан записать их в data descriptor ПОСЛЕ содержимого.
        Если бы буфер притворился перематываемым (появился seek), архив
        собрался бы с враньём в заголовках, и testzip нашёл бы это."""
        documents = [self.make_document(f'файл-{i}.bin', b'x' * 5000) for i in range(3)]

        archive = read_archive(self.download(documents))

        self.assertIsNone(archive.testzip(), 'битые записи в архиве')
        for info in archive.infolist():
            self.assertEqual(info.file_size, 5000)

    def test_duplicate_names_do_not_overwrite_each_other(self):
        """Дедупликация делает одинаковые имена нормой: один и тот же
        документ может лежать под своим именем в двух папках. В zip два
        одинаковых имени оставили бы пользователю один файл вместо двух."""
        first = self.make_document('договор.pdf', b'FIRST')
        second = self.make_document('договор.pdf', b'SECOND')

        archive = read_archive(self.download([first, second]))

        self.assertEqual(len(archive.namelist()), 2)
        self.assertIn('договор.pdf', archive.namelist())
        self.assertIn('договор (2).pdf', archive.namelist())
        self.assertEqual({archive.read(name) for name in archive.namelist()}, {b'FIRST', b'SECOND'})

    def test_path_in_original_name_cannot_escape_the_archive(self):
        """original_name приходит из браузера, и в нём может оказаться что
        угодно, вплоть до '../../'. Распаковщик, доверяющий путям из архива,
        записал бы такой файл мимо целевой папки (zip slip)."""
        document = self.make_document('обычное.pdf', b'PAYLOAD')
        document.file_object.original_name = '../../../etc/passwd'
        document.file_object.save(update_fields=['original_name'])

        archive = read_archive(self.download([document]))

        self.assertEqual(archive.namelist(), ['passwd'])

    def test_response_is_streaming_and_named(self):
        document = self.make_document('отчёт.pdf', b'REPORT')

        response = self.download([document])

        self.assertTrue(response.streaming, 'архив обязан отдаваться потоком, а не целиком из памяти')
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn("filename*=utf-8''", response['Content-Disposition'])
        self.assertNotIn('Content-Length', response)

    def test_files_not_in_the_selection_are_not_included(self):
        wanted = self.make_document('нужный.pdf', b'WANTED')
        self.make_document('лишний.pdf', b'UNWANTED')

        archive = read_archive(self.download([wanted]))

        self.assertEqual(archive.namelist(), ['нужный.pdf'])

    def test_trashed_documents_are_skipped(self):
        alive = self.make_document('живой.pdf', b'ALIVE')
        trashed = self.make_document('удалённый.pdf', b'TRASHED')
        trashed.is_deleted = True
        trashed.save(update_fields=['is_deleted'])

        archive = read_archive(self.download([alive, trashed]))

        self.assertEqual(archive.namelist(), ['живой.pdf'])


class BulkDownloadLimitsTest(TestCase):
    """Пределы. Архив собирается воркером Daphne, а не отдаётся nginx'ом,
    поэтому «выделить всё в общем каталоге и скачать» без ограничения было
    бы самообслуживаемым отказом портала."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='zip-limit-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='zip_limit_user', password='pass12345')
        self.client.force_login(self.user)

    def make_documents(self, count, size=10):
        documents = []
        for index in range(count):
            file_object = StorageService.upload(
                SimpleUploadedFile(f'файл-{index}.bin', bytes([index % 256]) * size),
                user=self.user, category=FileObject.Category.CATALOG,
            )
            documents.append(CatalogDocument.objects.create(
                file_object=file_object, title=f'файл-{index}', uploaded_by=self.user,
            ))
        return documents

    def request(self, documents, **params):
        params['ids'] = ','.join(str(document.pk) for document in documents)
        return self.client.get(reverse('catalog_bulk_download'), params)

    @override_settings(STORAGE_ZIP_MAX_FILES=2)
    def test_too_many_files_is_refused_with_a_reason(self):
        documents = self.make_documents(3)

        response = self.request(documents)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Слишком много файлов', response.json()['error'])

    @override_settings(STORAGE_ZIP_MAX_TOTAL_SIZE=15)
    def test_too_large_archive_is_refused_with_a_reason(self):
        documents = self.make_documents(2, size=10)

        response = self.request(documents)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Слишком большой архив', response.json()['error'])

    def test_empty_selection_is_refused_with_a_reason(self):
        """Не 200 с пустым архивом: пустой zip выглядел бы как «скачалось,
        но файлы пропали»."""
        response = self.client.get(reverse('catalog_bulk_download'), {'ids': ''})

        self.assertEqual(response.status_code, 400)
        self.assertIn('Нечего скачивать', response.json()['error'])

    def test_check_mode_reports_the_summary_without_building_the_archive(self):
        """?check=1 — то, что кнопка спрашивает ПЕРЕД навигацией: за самим
        архивом браузер уходит обычной ссылкой, и показать причину отказа
        в этот момент уже нечем."""
        documents = self.make_documents(2, size=10)

        response = self.request(documents, check='1')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['total_size'], 20)
        self.assertEqual(payload['skipped'], 0)
        self.assertFalse(getattr(response, 'streaming', False))

    @override_settings(STORAGE_ZIP_MAX_FILES=1)
    def test_check_mode_refuses_before_the_download_starts(self):
        documents = self.make_documents(2)

        response = self.request(documents, check='1')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    def test_limits_are_read_at_call_time(self):
        """Настройки storage читаются при вызове, а не константами уровня
        модуля — иначе override_settings на них не действовал бы, а смена
        предела требовала перезапуска процесса (ARCHITECTURE.md, раздел 9)."""
        with override_settings(STORAGE_ZIP_MAX_FILES=7, STORAGE_ZIP_MAX_TOTAL_SIZE=77):
            self.assertEqual(StorageService.get_archive_limits(), (7, 77))


class BulkDownloadPermissionsTest(TestCase):
    """Сужение списка по правам — на стороне модуля-потребителя.

    Самое опасное место фичи: id перечислены в query-строке, и без фильтра
    достаточно подставить чужие номера, чтобы выгрузить архивом закрытые
    папки приватного доступа.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='zip-perm-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.member = User.objects.create_user(username='zip_member', password='pass12345')
        self.outsider = User.objects.create_user(username='zip_outsider', password='pass12345')

        self.folder = DepartmentFolder.objects.create(name='Закрытая папка', created_by=self.member)
        self.folder.allowed_users.set([self.member])

        file_object = StorageService.upload(
            SimpleUploadedFile('секрет.pdf', b'SECRET'),
            user=self.member, category=FileObject.Category.DOCUMENT,
        )
        self.document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Секрет', uploaded_by=self.member,
        )

    def test_member_gets_the_document(self):
        self.client.force_login(self.member)

        response = self.client.get(
            reverse('deptdocs_bulk_download'), {'ids': str(self.document.pk)},
        )

        self.assertEqual(read_archive(response).read('секрет.pdf'), b'SECRET')

    def test_outsider_gets_nothing_even_with_a_valid_id(self):
        client = Client()
        client.force_login(self.outsider)

        response = client.get(reverse('deptdocs_bulk_download'), {'ids': str(self.document.pk)})

        self.assertEqual(response.status_code, 400)
        self.assertIn('Нечего скачивать', response.json()['error'])

    def test_check_mode_reports_inaccessible_files_as_skipped(self):
        """Частично доступный выбор не должен молча отдавать неполный архив:
        клиент показывает «пропущено N файлов без доступа»."""
        self.client.force_login(self.member)

        own_folder = DepartmentFolder.objects.create(name='Своя', created_by=self.member)
        own_folder.allowed_users.set([self.member])
        file_object = StorageService.upload(
            SimpleUploadedFile('своё.pdf', b'MINE'),
            user=self.member, category=FileObject.Category.DOCUMENT,
        )
        mine = DepartmentDocument.objects.create(
            folder=own_folder, file_object=file_object, title='Своё', uploaded_by=self.member,
        )

        closed_folder = DepartmentFolder.objects.create(name='Чужая', created_by=self.outsider)
        closed_folder.allowed_users.set([self.outsider])
        closed_object = StorageService.upload(
            SimpleUploadedFile('чужое.pdf', b'THEIRS'),
            user=self.outsider, category=FileObject.Category.DOCUMENT,
        )
        theirs = DepartmentDocument.objects.create(
            folder=closed_folder, file_object=closed_object, title='Чужое', uploaded_by=self.outsider,
        )

        response = self.client.get(reverse('deptdocs_bulk_download'), {
            'ids': f'{mine.pk},{theirs.pk}', 'check': '1',
        })

        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['skipped'], 1)

    def test_anonymous_is_sent_to_login(self):
        response = Client().get(reverse('exchange_bulk_download'), {'ids': '1'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])


class ExchangeBulkDownloadTest(TestCase):
    """Обменник: содержимое видно всем сотрудникам, ограничение только на
    удаление и перенос — поэтому архив собирается из чужой папки тоже, ровно
    как и одиночное скачивание."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='zip-exchange-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.owner = User.objects.create_user(username='zip_owner', password='pass12345')
        self.colleague = User.objects.create_user(username='zip_colleague', password='pass12345')

        file_object = StorageService.upload(
            SimpleUploadedFile('общий.pdf', b'SHARED'),
            user=self.owner, category=FileObject.Category.EXCHANGE,
        )
        self.file = ExchangeFile.objects.create(
            file_object=file_object, owner=self.owner, uploaded_by=self.owner,
        )

    def test_colleague_can_download_from_someone_elses_folder(self):
        self.client.force_login(self.colleague)

        response = self.client.get(reverse('exchange_bulk_download'), {'ids': str(self.file.pk)})

        self.assertEqual(read_archive(response).read('общий.pdf'), b'SHARED')
