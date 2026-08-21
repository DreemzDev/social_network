"""Просмотр справочников в браузере: PDF-копия офисного документа.

До этого .docx-справочник можно было только скачать — страница честно
показывала карточку «браузер показать не может». Здесь проверяется вся
цепочка: файл ставится в очередь, копия появляется, страница переключается
на просмотр — и, главное, что копия не подменяет оригинал и не переживает
замену файла.

Сам LibreOffice не запускается ни в одном тесте: он внешняя программа,
которой на машине разработчика может не быть, а проверять надо свой код
вокруг него. Поэтому запуск подменяется, а формирование команды
проверяется отдельно.
"""

import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from phonebook.models import Phonebook
from phonebook.tasks import convert_phonebook_to_pdf
from storage.convert import ConversionError, convert_to_pdf, is_convertible, soffice_path
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()

# Минимальный PDF: достаточно того, что содержимое непустое и уникальное —
# читать его никто не будет, важен только факт появления файла.
PDF_BYTES = b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'


def fake_convert(pdf_bytes=PDF_BYTES):
    """Подмена LibreOffice: кладёт готовый PDF туда, куда его положил бы он."""
    def convert(source_path, original_name, target_dir):
        produced = os.path.join(target_dir, 'source.pdf')
        with open(produced, 'wb') as target:
            target.write(pdf_bytes)
        return produced

    return convert


class ConversionTestCase(TestCase):
    """Свой MEDIA_ROOT на каждый тест: конвертация пишет настоящие файлы, и
    прогон не должен зависеть от того, что осталось от соседнего теста."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='portal-pdf-conversion-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='book_keeper', password='pass12345')
        self.client.force_login(self.user)

    def upload(self, name='Справочник.docx', content='фиктивный docx'.encode()):
        uploaded = SimpleUploadedFile(name, content, content_type='application/octet-stream')
        return StorageService.upload(
            uploaded, user=self.user, category=FileObject.Category.CATALOG,
        )

    def book(self, name='Справочник.docx', **kwargs):
        return Phonebook.objects.create(
            title='Телефоны', file_object=self.upload(name), created_by=self.user, **kwargs
        )


class ConvertibilityTest(TestCase):

    def test_office_documents_are_convertible_and_pdf_is_not(self):
        self.assertTrue(is_convertible('Справочник.docx'))
        self.assertTrue(is_convertible('Телефоны.XLSX'))
        self.assertFalse(is_convertible('Справочник.pdf'))
        self.assertFalse(is_convertible('архив.zip'))
        self.assertFalse(is_convertible('без_расширения'))

    @override_settings(STORAGE_SOFFICE_PATH=r'C:\nowhere\soffice.exe')
    def test_configured_path_that_does_not_exist_counts_as_missing(self):
        """Иначе портал предлагал бы кнопку «Показать в браузере», которая
        каждый раз падала бы в фоне, а пользователь видел бы только
        бесконечное «готовим версию для просмотра»."""
        self.assertIsNone(soffice_path())


class ConvertCommandTest(TestCase):
    """Как именно вызывается soffice. Проверяется без него самого."""

    def test_source_is_copied_with_extension(self):
        """LibreOffice выбирает фильтр по расширению, а blob на диске
        называется контрольной суммой без него — без копии с расширением
        конвертация падала бы на любом файле."""
        with tempfile.TemporaryDirectory() as work_dir:
            source = os.path.join(work_dir, 'blob_without_extension')
            with open(source, 'wb') as handle:
                handle.write(b'x')

            captured = {}

            def fake_run(command, **kwargs):
                captured['command'] = command
                # soffice кладёт результат рядом с исходником в --outdir
                outdir = command[command.index('--outdir') + 1]
                with open(os.path.join(outdir, 'source.pdf'), 'wb') as produced:
                    produced.write(PDF_BYTES)
                return mock.Mock(returncode=0, stdout=b'', stderr=b'')

            with override_settings(STORAGE_SOFFICE_PATH=__file__):
                with mock.patch('storage.convert.subprocess.run', fake_run):
                    result = convert_to_pdf(source, 'Справочник.DOCX', work_dir)

            self.assertTrue(result.endswith('.pdf'))
            self.assertTrue(any(part.endswith('source.docx') for part in captured['command']))
            self.assertIn('--headless', captured['command'])

    def test_zero_exit_code_without_pdf_is_an_error(self):
        """soffice отдаёт 0 и когда ничего не сконвертировал; поверь ему —
        и задача отчиталась бы об успехе, не создав файла."""
        with tempfile.TemporaryDirectory() as work_dir:
            source = os.path.join(work_dir, 'blob')
            with open(source, 'wb') as handle:
                handle.write(b'x')

            with override_settings(STORAGE_SOFFICE_PATH=__file__):
                with mock.patch(
                    'storage.convert.subprocess.run',
                    return_value=mock.Mock(returncode=0, stdout=b'', stderr=b'Error: source file could not be loaded'),
                ):
                    with self.assertRaises(ConversionError) as caught:
                        convert_to_pdf(source, 'Справочник.docx', work_dir)

            self.assertIn('could not be loaded', str(caught.exception))

    def test_missing_libreoffice_reports_reason(self):
        with tempfile.TemporaryDirectory() as work_dir:
            with override_settings(STORAGE_SOFFICE_PATH=None):
                with mock.patch('storage.convert.shutil.which', return_value=None):
                    with mock.patch('storage.convert.os.path.exists', return_value=False):
                        with self.assertRaises(ConversionError) as caught:
                            convert_to_pdf('anything', 'Справочник.docx', work_dir)

        self.assertIn('LibreOffice', str(caught.exception))


class ConversionTaskTest(ConversionTestCase):

    def test_successful_conversion_attaches_pdf_and_keeps_original(self):
        book = self.book()
        original_id = book.file_object_id

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            result = convert_phonebook_to_pdf(book.pk, original_id, self.user.pk)

        book.refresh_from_db()
        self.assertEqual(result, 'done')
        self.assertEqual(book.conversion_status, Phonebook.Conversion.DONE)
        self.assertEqual(book.file_object_id, original_id, 'оригинал подменять нельзя')
        self.assertTrue(book.is_previewable)
        self.assertEqual(book.pdf_file_object.original_name, 'Справочник.pdf')
        self.assertEqual(book.preview_object, book.pdf_file_object)

    def test_failure_is_recorded_with_reason(self):
        """Молчаливый отказ здесь особенно вреден: страница осталась бы в
        состоянии «готовим версию для просмотра» навсегда."""
        book = self.book()

        def explode(*args, **kwargs):
            raise ConversionError('файл повреждён')

        with mock.patch('phonebook.tasks.convert_to_pdf', explode):
            result = convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)

        book.refresh_from_db()
        self.assertEqual(result, 'failed')
        self.assertEqual(book.conversion_status, Phonebook.Conversion.FAILED)
        self.assertEqual(book.conversion_error, 'файл повреждён')
        self.assertIsNone(book.pdf_file_object)

    def test_result_is_dropped_if_file_was_replaced_meanwhile(self):
        """Конвертация идёт секунды, за это время справочник могли заменить.
        Без сверки file_object к новому файлу прикрепился бы PDF от старого,
        и сотрудник читал бы прошлогодний справочник, не зная об этом."""
        book = self.book()
        stale_file_id = book.file_object_id

        book.file_object = self.upload(name='Новый.docx', content='другой docx'.encode())
        book.save(update_fields=['file_object'])

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            result = convert_phonebook_to_pdf(book.pk, stale_file_id, self.user.pk)

        book.refresh_from_db()
        self.assertEqual(result, 'skipped')
        self.assertIsNone(book.pdf_file_object)

    def test_second_conversion_releases_the_previous_pdf(self):
        book = self.book()

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)
        book.refresh_from_db()
        first_pdf_blob_id = book.pdf_file_object.blob_id

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert(PDF_BYTES + b'v2')):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)
        book.refresh_from_db()

        self.assertNotEqual(book.pdf_file_object.blob_id, first_pdf_blob_id)
        self.assertEqual(
            FileBlob.objects.get(pk=first_pdf_blob_id).status, FileBlob.Status.ORPHAN,
            'старая копия осталась бы ACTIVE без единой ссылки',
        )


class ConversionViewsTest(ConversionTestCase):

    def test_convert_button_starts_the_task(self):
        book = self.book()

        with mock.patch('phonebook.views.converter_available', return_value=True):
            with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse('phonebook_convert', args=[book.pk]))

        book.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(book.conversion_status, Phonebook.Conversion.DONE)

    def test_pdf_book_is_refused_with_a_reason(self):
        book = self.book(name='Справочник.pdf')

        response = self.client.post(reverse('phonebook_convert', args=[book.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertIn('открывается в браузере', response.json()['error'])

    def test_without_libreoffice_the_answer_says_so(self):
        book = self.book()

        with mock.patch('phonebook.views.converter_available', return_value=False):
            response = self.client.post(reverse('phonebook_convert', args=[book.pk]))

        self.assertEqual(response.status_code, 503)
        self.assertIn('LibreOffice', response.json()['error'])

    def test_status_endpoint_reports_readiness(self):
        book = self.book()

        response = self.client.get(reverse('phonebook_conversion_status', args=[book.pk]))
        self.assertEqual(response.json()['previewable'], False)

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)

        response = self.client.get(reverse('phonebook_conversion_status', args=[book.pk]))
        self.assertEqual(response.json()['previewable'], True)
        self.assertEqual(response.json()['status'], Phonebook.Conversion.DONE)

    def test_view_serves_pdf_but_original_flag_serves_the_source(self):
        """Кнопка «Скачать файл» обязана отдавать загруженный документ:
        пользователю нужен .docx, который можно править, а не пересборка."""
        book = self.book()
        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)

        with override_settings(DEBUG=True):
            preview = self.client.get(reverse('phonebook_view_file', args=[book.pk]))
            original = self.client.get(
                reverse('phonebook_view_file', args=[book.pk]) + '?original=1'
            )

        self.assertEqual(b''.join(preview.streaming_content), PDF_BYTES)
        self.assertEqual(b''.join(original.streaming_content), 'фиктивный docx'.encode())

    def test_page_shows_iframe_after_conversion(self):
        book = self.book()

        response = self.client.get(reverse('phonebook', args=[book.pk]))
        self.assertNotContains(response, '<iframe')

        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)

        response = self.client.get(reverse('phonebook', args=[book.pk]))
        self.assertContains(response, '<iframe')

    def test_button_is_absent_without_libreoffice(self):
        """Контрол либо работает, либо его нет в разметке (ARCHITECTURE 12.4):
        кнопка без LibreOffice на сервере только обещала бы просмотр."""
        book = self.book()

        with mock.patch('phonebook.views.converter_available', return_value=False):
            response = self.client.get(reverse('phonebook', args=[book.pk]))
        self.assertNotContains(response, 'id="phonebook-convert"')

        with mock.patch('phonebook.views.converter_available', return_value=True):
            response = self.client.get(reverse('phonebook', args=[book.pk]))
        self.assertContains(response, 'id="phonebook-convert"')

    def test_replacing_file_drops_the_stale_pdf(self):
        book = self.book()
        with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert()):
            convert_phonebook_to_pdf(book.pk, book.file_object_id, self.user.pk)
        book.refresh_from_db()
        stale_pdf_blob_id = book.pdf_file_object.blob_id

        replacement = SimpleUploadedFile(
            'Новый.docx', 'новый docx'.encode(), content_type='application/octet-stream',
        )
        with mock.patch('phonebook.views.converter_available', return_value=True):
            with mock.patch('phonebook.tasks.convert_to_pdf', fake_convert(PDF_BYTES + b'v2')):
                with self.captureOnCommitCallbacks(execute=True):
                    self.client.post(
                        reverse('phonebook', args=[book.pk]),
                        {'title': 'Телефоны', 'book': replacement},
                    )

        book.refresh_from_db()
        self.assertNotEqual(book.pdf_file_object.blob_id, stale_pdf_blob_id)
        self.assertEqual(
            FileBlob.objects.get(pk=stale_pdf_blob_id).status, FileBlob.Status.ORPHAN,
        )
