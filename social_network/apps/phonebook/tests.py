"""Справочники: доступ, подписи и мягкое удаление.

Главный тест здесь — про анонима: страницы справочника отвечали 200 без
входа, то есть отдавали внутренний документ организации кому угодно и
принимали POST на переименование.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from phonebook.context_processors import reference_books
from phonebook.forms import PhonebookCreateForm
from phonebook.models import Phonebook
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class PhonebookTestCase(TestCase):
    """Свой MEDIA_ROOT на каждый тест: файлы пишутся на диск, и общий
    каталог делал бы результат зависимым от порядка прогона."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='book_author', password='pass12345')
        self.other = User.objects.create_user(username='book_editor', password='pass12345')

    def make_book(self, title='Справочник', name='book.pdf', content=b'%PDF-1.4 test'):
        file_object = StorageService.upload(
            SimpleUploadedFile(name, content), user=self.user, category=FileObject.Category.CATALOG,
        )
        return Phonebook.objects.create(title=title, file_object=file_object, created_by=self.user)


class AnonymousAccessTest(PhonebookTestCase):
    """Дыра, найденная 18.08.2026: /phonebook/<id>/ и /view/ отвечали 200
    без входа. Первый принимал ещё и POST — аноним мог переименовать
    справочник организации, второй отдавал сам файл.

    Портал закрыт от неаутентифицированных (коммит b85b13e5), эти два
    адреса тогда пропустили: они лежат в отдельном приложении.
    """

    def test_all_phonebook_urls_require_login(self):
        book = self.make_book()
        urls = [
            reverse('phonebook', args=[book.pk]),
            reverse('phonebook_view_file', args=[book.pk]),
            reverse('phonebook_add'),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login', response['Location'])

    def test_anonymous_cannot_rename_book(self):
        book = self.make_book(title='Было')

        self.client.post(reverse('phonebook', args=[book.pk]), {'title': 'Стало'})

        book.refresh_from_db()
        self.assertEqual(book.title, 'Было')


class SignatureTest(PhonebookTestCase):
    """Править справочник может любой сотрудник, поэтому ответственность
    держится на подписи: без неё непонятно, кто подменил общий документ."""

    def test_create_records_author(self):
        self.client.force_login(self.user)

        self.client.post(reverse('phonebook_add'), {
            'title': 'Новый справочник',
            'book': SimpleUploadedFile('new.pdf', b'%PDF-1.4 new'),
        })

        book = Phonebook.objects.get(title='Новый справочник')
        self.assertEqual(book.created_by, self.user)
        self.assertIsNone(book.updated_by)
        self.assertIsNotNone(book.file_object)

    def test_edit_records_editor_without_losing_author(self):
        book = self.make_book(title='Общий')
        self.client.force_login(self.other)

        self.client.post(reverse('phonebook', args=[book.pk]), {'title': 'Общий (ред.)'})

        book.refresh_from_db()
        self.assertEqual(book.title, 'Общий (ред.)')
        self.assertEqual(book.created_by, self.user)
        self.assertEqual(book.updated_by, self.other)

    def test_replacing_file_detaches_the_old_one(self):
        book = self.make_book(name='old.pdf', content=b'%PDF-1.4 old')
        old_file_object = book.file_object
        self.client.force_login(self.other)

        self.client.post(reverse('phonebook', args=[book.pk]), {
            'title': book.title,
            'book': SimpleUploadedFile('fresh.pdf', b'%PDF-1.4 fresh'),
        })

        book.refresh_from_db()
        self.assertNotEqual(book.file_object_id, old_file_object.pk)
        self.assertFalse(FileObject.objects.filter(pk=old_file_object.pk).exists())


class SoftDeleteTest(PhonebookTestCase):
    """Удаление — пометка, а не строка из БД: иначе увидеть, кто убрал
    справочник из меню, было бы негде."""

    def test_delete_marks_and_records_who(self):
        book = self.make_book()
        self.client.force_login(self.other)

        response = self.client.post(reverse('phonebook_delete', args=[book.pk]))

        self.assertEqual(response.status_code, 200)
        book.refresh_from_db()
        self.assertTrue(book.is_deleted)
        self.assertEqual(book.deleted_by, self.other)
        self.assertIsNotNone(book.deleted_at)
        self.assertIsNotNone(book.file_object, 'файл остаётся: удаление обратимо')

    def test_deleted_book_disappears_from_menu_and_page(self):
        book = self.make_book()
        self.client.force_login(self.user)
        self.client.post(reverse('phonebook_delete', args=[book.pk]))

        response = self.client.get(reverse('phonebook', args=[book.pk]))
        self.assertEqual(response.status_code, 404)

        request = RequestFactory().get('/')
        request.user = self.user
        self.assertNotIn(book, list(reference_books(request)['menu_books']))

    def test_restore_returns_book_to_menu(self):
        book = self.make_book()
        self.client.force_login(self.user)
        self.client.post(reverse('phonebook_delete', args=[book.pk]))

        self.client.post(reverse('phonebook_restore', args=[book.pk]))

        book.refresh_from_db()
        self.assertFalse(book.is_deleted)
        self.assertIsNone(book.deleted_by)


class UploadValidationTest(PhonebookTestCase):
    """Проверка расширения — на сервере. Атрибут accept у <input> только
    подсказывает проводнику, какие файлы показать, и обходится руками."""

    def test_executable_is_rejected(self):
        form = PhonebookCreateForm(
            data={'title': 'Установщик'},
            files={'book': SimpleUploadedFile('setup.exe', b'MZ')},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('book', form.errors)

    def test_word_and_astra_office_are_accepted(self):
        for name in ('doc.docx', 'doc.odt', 'doc.pdf', 'table.ods'):
            with self.subTest(name=name):
                form = PhonebookCreateForm(
                    data={'title': 'Документ'},
                    files={'book': SimpleUploadedFile(name, b'data')},
                )
                self.assertTrue(form.is_valid(), form.errors)

    def test_book_is_required_when_creating(self):
        form = PhonebookCreateForm(data={'title': 'Без файла'}, files={})
        self.assertFalse(form.is_valid())


class PreviewTest(PhonebookTestCase):
    """Страница обязана честно сказать, что Word она показать не может:
    пустой <iframe> выглядел бы как сломанная страница."""

    def test_pdf_is_previewable_and_word_is_not(self):
        pdf = self.make_book(title='PDF', name='doc.pdf')
        word = self.make_book(title='Word', name='doc.docx', content=b'PK word')

        self.assertTrue(pdf.is_previewable)
        self.assertFalse(word.is_previewable)

    def test_page_offers_download_instead_of_empty_iframe(self):
        word = self.make_book(title='Word', name='doc.docx', content=b'PK word')
        self.client.force_login(self.user)

        response = self.client.get(reverse('phonebook', args=[word.pk]))

        self.assertContains(response, 'Скачать файл')
        self.assertNotContains(response, '<iframe')
