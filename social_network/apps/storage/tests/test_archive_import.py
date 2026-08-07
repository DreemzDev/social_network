"""Загрузка zip с распаковкой на портале.

Мотивация фичи прикладная: действующие документы лежат в расшаренных
сетевых папках, и переносить их по одному никто не станет. Отсюда главный
тест этого модуля — не «файлы появились», а «имена не превратились в
мусор»: архивы придут именно из тех папок, то есть собранные windows-
архиваторами, у которых русские имена записаны в cp866 без флага UTF-8.

Остальное — проверки того, что архив от пользователя не считается
доверенным: пути с '..', zip-бомба, пароль, враньё в оглавлении о размере.
"""

import io
import shutil
import tempfile
import zipfile
from contextlib import ExitStack
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile, ExchangeFolder
from storage.archives import (
    decode_entry_name,
    extract_archive,
    inspect_archive,
    split_entry_path,
)
from storage.exceptions import InvalidArchiveError
from storage.models import FileBlob, FileObject

User = get_user_model()


def make_archive(files, *, utf8_names=True) -> bytes:
    """Собирает zip в памяти.

    utf8_names=False имитирует старый windows-архиватор: имя записано
    байтами cp866, флаг «имена в UTF-8» не выставлен. Штатным API zipfile
    такой архив не собрать — он сам решает кодировку имени (ASCII как есть,
    иначе UTF-8 и флаг 0x800), поэтому на время сборки подменяется ровно
    та функция, которая это решает. Без подмены тест проверял бы не то:
    имя уехало бы в UTF-8 с флагом, то есть в заведомо исправном виде.
    """
    buffer = io.BytesIO()

    def as_cp866(self):
        return self.filename.encode('cp866'), self.flag_bits

    with ExitStack() as stack:
        if not utf8_names:
            stack.enter_context(
                mock.patch.object(zipfile.ZipInfo, '_encodeFilenameFlags', as_cp866)
            )
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                archive.writestr(name, content)

    return buffer.getvalue()


def make_encrypted_archive(files) -> bytes:
    """Архив с выставленным признаком шифрования.

    Через writestr этого не сделать: ZipFile._open_to_write сбрасывает
    flag_bits в ноль перед записью каждой записи. Поэтому бит 0 «запись
    зашифрована» ставится прямо в заголовках готового архива — в локальном
    (смещение 6 от сигнатуры PK\\x03\\x04) и в центральном каталоге
    (смещение 8 от PK\\x01\\x02).
    """
    raw = bytearray(make_archive(files))

    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        position = raw.find(signature)
        while position != -1:
            raw[position + offset] |= 0x01
            position = raw.find(signature, position + 1)

    return bytes(raw)


def upload_file(data, name='archive.zip'):
    return SimpleUploadedFile(name, data, content_type='application/zip')


class ArchiveNameDecodingTest(SimpleTestCase):
    """Кодировка имён — главная практическая грабля всей фичи."""

    def test_cp866_names_from_windows_archivers_are_readable(self):
        """Без перекодировки «Приказы» приезжает как «╧ЁштъЁ√»: zipfile по
        стандарту декодирует имена без флага 0x800 как cp437, а windows-
        архиваторы писали их в cp866. Это ровно те архивы, ради которых
        фича и делается — их собирали на рабочих станциях."""
        data = make_archive([('Приказ №5.pdf', b'x')], utf8_names=False)

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.infolist()[0]

        self.assertNotEqual(info.filename, 'Приказ №5.pdf', 'тест не воспроизводит проблему')
        self.assertEqual(decode_entry_name(info), 'Приказ №5.pdf')

    def test_utf8_names_are_left_alone(self):
        data = make_archive([('Отчёт.pdf', b'x')], utf8_names=True)

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.infolist()[0]

        self.assertEqual(decode_entry_name(info), 'Отчёт.pdf')

    def test_ascii_names_survive_both_paths(self):
        """Для ASCII cp437 и cp866 совпадают, поэтому перекодировка обязана
        быть тождественной, а не портить латиницу."""
        for utf8 in (True, False):
            with self.subTest(utf8=utf8):
                data = make_archive([('report.pdf', b'x')], utf8_names=utf8)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertEqual(decode_entry_name(archive.infolist()[0]), 'report.pdf')


class ArchivePathSanitationTest(SimpleTestCase):

    def test_parent_traversal_is_rejected(self):
        """Имена из архива становятся названиями папок модуля — пускать в
        них обход дерева незачем."""
        self.assertIsNone(split_entry_path('../../../etc/passwd'))
        self.assertIsNone(split_entry_path('Папка/../../секрет.pdf'))

    def test_absolute_paths_are_flattened_not_rejected(self):
        """Ведущий слэш — не атака, а особенность архиватора; такой файл
        просто кладётся в целевую папку."""
        self.assertEqual(split_entry_path('/Приказы/файл.pdf'), (('Приказы',), 'файл.pdf'))

    def test_windows_separators_and_drives(self):
        self.assertEqual(split_entry_path('Приказы\\2024\\файл.pdf'), (('Приказы', '2024'), 'файл.pdf'))
        self.assertIsNone(split_entry_path('C:\\Users\\файл.pdf'))

    def test_service_junk_is_skipped(self):
        self.assertIsNone(split_entry_path('__MACOSX/._файл.pdf'))
        self.assertIsNone(split_entry_path('Папка/Thumbs.db'))
        self.assertIsNone(split_entry_path('.DS_Store'))

    def test_long_names_are_truncated_keeping_extension(self):
        """CharField у original_name — 255 символов; без обрезки вставка
        падала бы с DataError, а обрезка «в лоб» лишила бы файл расширения."""
        parts, name = split_entry_path('а' * 300 + '.pdf')

        self.assertEqual(len(name), 255)
        self.assertTrue(name.endswith('.pdf'))


class ArchiveInspectionTest(SimpleTestCase):
    """Проверки, отбивающие архив ДО того, как что-то попадёт на диск."""

    def test_not_an_archive(self):
        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(b'%PDF-1.4 not a zip'))
        self.assertIn('не является zip', caught.exception.message)

    def test_empty_archive(self):
        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(make_archive([])))
        self.assertIn('нет файлов', caught.exception.message)

    def test_encrypted_archive_is_refused_with_a_reason(self):
        """Пароль спросить негде. Молча пропустить такие записи нельзя:
        пользователь получил бы пустую папку без объяснения."""
        data = make_encrypted_archive([('секрет.pdf', b'payload')])

        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(data))
        self.assertIn('паролем', caught.exception.message)

    @override_settings(STORAGE_ARCHIVE_MAX_FILES=2)
    def test_too_many_files(self):
        data = make_archive([(f'{i}.txt', b'x') for i in range(3)])

        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(data))
        self.assertIn('при пределе 2', caught.exception.message)

    @override_settings(STORAGE_ARCHIVE_MAX_TOTAL_SIZE=100)
    def test_too_large_unpacked(self):
        data = make_archive([('big.bin', b'x' * 500)])

        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(data))
        self.assertIn('распакованном виде', caught.exception.message)

    @override_settings(STORAGE_ARCHIVE_MAX_RATIO=5, STORAGE_ARCHIVE_MAX_TOTAL_SIZE=10 ** 9)
    def test_zip_bomb_is_caught_by_ratio_before_reading_content(self):
        """Отказ по оглавлению: мегабайт нулей сжимается в сотни байт, и
        предел по объёму поймал бы это только после чтения потока."""
        data = make_archive([('bomb.bin', b'\0' * 1024 * 1024)])

        with self.assertRaises(InvalidArchiveError) as caught:
            inspect_archive(io.BytesIO(data))
        self.assertIn('бомба', caught.exception.message)

    def test_directory_entries_are_not_counted_as_files(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('Папка/', b'')
            archive.writestr('Папка/файл.pdf', b'x')

        entries = inspect_archive(io.BytesIO(buffer.getvalue()))

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, 'файл.pdf')
        self.assertEqual(entries[0].parts, ('Папка',))


class CatalogArchiveImportTest(TestCase):
    """Сквозной путь: загрузка через эндпоинт → распаковка → документы.

    CELERY_TASK_ALWAYS_EAGER под тестами выполняет задачу синхронно, так
    что к моменту ответа всё уже распаковано.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='archive-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='archive_user', password='pass12345')
        self.client.force_login(self.user)

    def upload(self, files, *, folder=None, utf8_names=True, name='archive.zip'):
        payload = {'archive': upload_file(make_archive(files, utf8_names=utf8_names), name)}
        if folder is not None:
            payload['folder_id'] = folder.pk
        return self.client.post(reverse('catalog_upload_archive'), payload)

    def test_flat_archive_becomes_documents(self):
        response = self.upload([('первый.pdf', b'ONE'), ('второй.pdf', b'TWO')])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 2)
        self.assertEqual(
            sorted(CatalogDocument.objects.values_list('title', flat=True)),
            ['второй.pdf', 'первый.pdf'],
        )

    def test_nested_folders_are_recreated(self):
        """Ради этого фича и нужна: структура сетевой папки должна приехать
        на портал целиком, а не свалиться в одну кучу."""
        self.upload([
            ('Приказы/2024/приказ.pdf', b'ORDER'),
            ('Приказы/бланк.docx', b'FORM'),
            ('корневой.txt', b'ROOT'),
        ])

        orders = CatalogFolder.objects.get(name='Приказы', parent=None)
        year = CatalogFolder.objects.get(name='2024', parent=orders)

        self.assertEqual(CatalogDocument.objects.get(title='приказ.pdf').folder, year)
        self.assertEqual(CatalogDocument.objects.get(title='бланк.docx').folder, orders)
        self.assertIsNone(CatalogDocument.objects.get(title='корневой.txt').folder)

    def test_cp866_names_arrive_readable_end_to_end(self):
        self.upload([('Приказы/Приказ №5.pdf', b'ORDER')], utf8_names=False)

        self.assertTrue(CatalogFolder.objects.filter(name='Приказы').exists())
        self.assertTrue(CatalogDocument.objects.filter(title='Приказ №5.pdf').exists())

    def test_unpacked_into_the_current_folder(self):
        folder = CatalogFolder.objects.create(name='Целевая', created_by=self.user)

        self.upload([('док.pdf', b'DOC')], folder=folder)

        self.assertEqual(CatalogDocument.objects.get(title='док.pdf').folder, folder)

    def test_repeated_upload_reuses_folders_instead_of_duplicating_them(self):
        """Повторная загрузка того же архива должна долить файлы в
        существующие папки, а не завести рядом вторую «Приказы»."""
        self.upload([('Приказы/а.pdf', b'A')])
        self.upload([('Приказы/б.pdf', b'B')])

        self.assertEqual(CatalogFolder.objects.filter(name='Приказы').count(), 1)
        self.assertEqual(CatalogDocument.objects.count(), 2)

    def test_identical_content_is_deduplicated(self):
        """Главная выгода переноса именно через storage: в сетевых папках
        один документ лежит в десятке копий, на портал он ляжет одной."""
        self.upload([('копия1.pdf', b'SAME'), ('Папка/копия2.pdf', b'SAME')])

        self.assertEqual(CatalogDocument.objects.count(), 2)
        self.assertEqual(FileBlob.objects.filter(size=len(b'SAME')).count(), 1)

    def test_archive_itself_does_not_stay_in_the_catalog(self):
        """Архив был способом донести файлы, а не документом. Он не должен
        ни висеть в каталоге, ни остаться ACTIVE без ссылок — это ровно тот
        blob, который живёт вечно (ARCHITECTURE.md, 5.5)."""
        self.upload([('док.pdf', b'DOC')])

        self.assertFalse(CatalogDocument.objects.filter(title__endswith='.zip').exists())
        archive_blobs = FileBlob.objects.filter(
            file_objects__original_name='archive.zip',
        )
        self.assertFalse(archive_blobs.filter(status=FileBlob.Status.ACTIVE).exists())

    def test_files_are_stored_through_storage_service(self):
        """Не запись на диск мимо storage: у каждого документа обязан быть
        FileObject с checksum, иначе не работают ни дедупликация, ни квота,
        ни аудит."""
        self.upload([('док.pdf', b'DOC CONTENT')])

        document = CatalogDocument.objects.get(title='док.pdf')
        self.assertEqual(len(document.file_object.blob.checksum), 64)
        self.assertEqual(document.file_object.category, FileObject.Category.CATALOG)

    def test_zip_slip_entry_is_skipped_not_imported(self):
        response = self.upload([('../../снаружи.pdf', b'X'), ('внутри.pdf', b'Y')])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(CatalogDocument.objects.values_list('title', flat=True)), ['внутри.pdf'],
        )

    def test_broken_archive_is_refused_before_anything_is_created(self):
        response = self.client.post(
            reverse('catalog_upload_archive'), {'archive': upload_file(b'not a zip at all')},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('не является zip', response.json()['error'])
        self.assertEqual(CatalogDocument.objects.count(), 0)
        self.assertEqual(FileBlob.objects.count(), 0, 'отказ не должен оставлять blob на диске')

    def test_missing_file_is_refused_with_a_reason(self):
        response = self.client.post(reverse('catalog_upload_archive'), {})

        self.assertEqual(response.status_code, 400)
        self.assertIn('не выбран', response.json()['error'])

    def test_anonymous_cannot_upload(self):
        response = Client().post(reverse('catalog_upload_archive'), {})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])


class ExtractSkipsBadEntriesTest(TestCase):
    """Один негодный файл не должен ронять весь импорт.

    Проверяется на уровне extract_archive, а не через эндпоинт: предел
    STORAGE_MAX_UPLOAD_SIZE действует и на сам файл архива, поэтому
    «архив, в котором одна запись слишком большая» через вьюху не собрать —
    архив отбился бы целиком, ещё не дойдя до распаковки.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='archive-skip-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='archive_skip_user', password='pass12345')

    @override_settings(STORAGE_MAX_UPLOAD_SIZE=64)
    def test_oversized_member_is_skipped_and_reported(self):
        created = []
        data = make_archive([('мелкий.txt', b'x' * 10), ('крупный.bin', b'y' * 500)])

        result = extract_archive(
            io.BytesIO(data),
            user=self.user,
            category=FileObject.Category.CATALOG,
            root_folder=None,
            ensure_folder=lambda parent, name: None,
            create_record=lambda file_object, folder, name: created.append(name),
        )

        self.assertEqual(created, ['мелкий.txt'])
        self.assertEqual(result['done'], 1)
        self.assertEqual(result['skipped'], 1)
        self.assertIn('слишком большой', result['reasons'][0])

    @override_settings(STORAGE_MAX_UPLOAD_SIZE=64)
    def test_oversized_member_leaves_nothing_behind(self):
        """Обрыв чтения по превышению не должен оставить ни записи в БД,
        ни половины файла: upload() до него просто не доходит."""
        data = make_archive([('крупный.bin', b'y' * 500)])

        extract_archive(
            io.BytesIO(data),
            user=self.user,
            category=FileObject.Category.CATALOG,
            root_folder=None,
            ensure_folder=lambda parent, name: None,
            create_record=lambda file_object, folder, name: None,
        )

        self.assertEqual(FileObject.objects.count(), 0)
        self.assertEqual(FileBlob.objects.count(), 0)

    def test_lying_header_does_not_abort_the_whole_import(self):
        """Размер в оглавлении заявляет тот, кто собрал архив.

        Сверку делает сам zipfile — прочитав меньше заявленного, он бросает
        BadZipFile по несходящемуся CRC, то есть до upload() такая запись не
        доходит. Проверяется здесь другое: что это не роняет ЗАДАЧУ. Без
        обработки исключения один битый файл обрывал распаковку целиком, и
        пользователь получал частичный импорт с непонятной ошибкой вместо
        отчёта «пропущено 1».
        """
        data = bytearray(make_archive([('обманка.bin', b'z' * 5000)]))

        # Подменяем размер распакованных данных в центральном каталоге:
        # PK\x01\x02 + 24 байта до поля uncompressed size.
        position = data.find(b'PK\x01\x02')
        data[position + 24:position + 28] = (10).to_bytes(4, 'little')

        result = extract_archive(
            io.BytesIO(bytes(data)),
            user=self.user,
            category=FileObject.Category.CATALOG,
            root_folder=None,
            ensure_folder=lambda parent, name: None,
            create_record=lambda file_object, folder, name: None,
        )

        self.assertEqual(result['done'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertIn('повреждена', result['reasons'][0])
        self.assertEqual(FileObject.objects.count(), 0)


class DeptdocsArchiveImportTest(TestCase):
    """У приватного доступа своя специфика: права живут на папке."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='archive-dept-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.member = User.objects.create_user(username='archive_member', password='pass12345')
        self.outsider = User.objects.create_user(username='archive_outsider', password='pass12345')

        self.folder = DepartmentFolder.objects.create(name='Закрытая', created_by=self.member)
        self.folder.allowed_users.set([self.member])

    def upload(self, client, files, folder):
        return client.post(reverse('deptdocs_upload_archive'), {
            'archive': upload_file(make_archive(files)), 'folder_id': folder.pk,
        })

    def test_subfolders_inherit_access_from_the_target_folder(self):
        """Папка с пустым allowed_users не видна НИКОМУ, включая её
        создателя — распакованные в неё документы просто исчезли бы из
        интерфейса."""
        self.client.force_login(self.member)

        self.upload(self.client, [('Вложенная/док.pdf', b'DOC')], self.folder)

        nested = DepartmentFolder.objects.get(name='Вложенная')
        self.assertTrue(nested.is_accessible_by(self.member))
        self.assertEqual(DepartmentDocument.objects.get(title='док.pdf').folder, nested)

    def test_outsider_cannot_unpack_into_a_closed_folder(self):
        client = Client()
        client.force_login(self.outsider)

        response = self.upload(client, [('док.pdf', b'DOC')], self.folder)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DepartmentDocument.objects.count(), 0)

    def test_folder_is_required(self):
        """Документ вне папки не наследует ничьих прав и не виден никому,
        поэтому распаковка «в корень» здесь запрещена."""
        self.client.force_login(self.member)

        response = self.client.post(reverse('deptdocs_upload_archive'), {
            'archive': upload_file(make_archive([('док.pdf', b'DOC')])),
        })

        self.assertEqual(response.status_code, 404)


class ExchangeArchiveImportTest(TestCase):

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='archive-exchange-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.owner = User.objects.create_user(username='archive_owner', password='pass12345')
        self.colleague = User.objects.create_user(username='archive_colleague', password='pass12345')

    def test_colleague_can_unpack_into_someone_elses_folder(self):
        """Обменник открыт на запись всем: положить архив в чужую папку
        можно ровно так же, как отдельный файл."""
        self.client.force_login(self.colleague)

        response = self.client.post(
            reverse('exchange_upload_archive', args=[self.owner.pk]),
            {'archive': upload_file(make_archive([('Проект/смета.xlsx', b'X')]))},
        )

        self.assertEqual(response.status_code, 200)
        exchange_file = ExchangeFile.objects.get()
        self.assertEqual(exchange_file.owner, self.owner, 'файл должен лечь в папку владельца')
        self.assertEqual(exchange_file.uploaded_by, self.colleague, 'загрузивший — коллега')

    def test_created_subfolder_belongs_to_the_folder_owner(self):
        """ExchangeFolder — не самостоятельная папка, а вложенность внутри
        чьей-то личной, поэтому owner у неё обязателен и равен владельцу
        личной папки, а не тому, кто распаковал."""
        self.client.force_login(self.colleague)

        self.client.post(
            reverse('exchange_upload_archive', args=[self.owner.pk]),
            {'archive': upload_file(make_archive([('Проект/смета.xlsx', b'X')]))},
        )

        folder = ExchangeFolder.objects.get(name='Проект')
        self.assertEqual(folder.owner, self.owner)
        self.assertEqual(folder.created_by, self.colleague)
