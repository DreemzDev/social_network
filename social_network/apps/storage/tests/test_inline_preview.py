"""?inline=1 на download-эндпоинтах трёх модулей — открыть файл в браузере
(PDF/картинку) вместо скачивания. StorageService.get_download_response уже
поддерживал inline=True (используется для phonebook), здесь просто
проброшен query-параметр в тех же вьюхах, права те же, что и на обычное
скачивание."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService
from storage.utils import is_inline_safe

User = get_user_model()


class InlinePreviewTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='preview_user', password='pass12345')
        self.client.force_login(self.user)

    def test_inline_param_sets_content_disposition_inline(self):
        uploaded = SimpleUploadedFile('report.pdf', b'pdf content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(file_object=file_object, title='Отчёт', uploaded_by=self.user)

        response = self.client.get(reverse('catalog_download', args=[document.pk]), {'inline': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('inline', response['Content-Disposition'])

    def test_without_inline_param_defaults_to_attachment(self):
        uploaded = SimpleUploadedFile('report2.pdf', b'pdf content 2', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(file_object=file_object, title='Отчёт 2', uploaded_by=self.user)

        response = self.client.get(reverse('catalog_download', args=[document.pk]))

        self.assertIn('attachment', response['Content-Disposition'])


class InlineMimeWhitelistTest(TestCase):
    """?inline=1 отдаётся только для заведомо пассивных типов.

    Эндпоинты скачивания всех трёх модулей пробрасывают inline из
    query-параметра, а mime_type определяется по ИМЕНИ загруженного файла
    (mimetypes.guess_type в StorageService.upload) — то есть его выбирает
    загрузивший. Без белого списка загруженный .html открывался бы как
    страница в домене портала со всеми правами текущего пользователя:
    обычная загрузка файла в обменник давала бы хранимую XSS.

    В интерфейсе пункт «Просмотр» для таких типов не показывается
    (utils.PREVIEWABLE_EXTENSIONS), поэтому проверка нужна именно на
    сервере — ссылку с ?inline=1 можно собрать руками.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='inline_mime_user', password='pass12345')
        self.client.force_login(self.user)

    def download(self, name, content, *, inline=True):
        uploaded = SimpleUploadedFile(name, content)
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title=name, uploaded_by=self.user,
        )
        params = {'inline': '1'} if inline else {}
        return self.client.get(reverse('catalog_download', args=[document.pk]), params)

    def test_html_is_forced_to_attachment(self):
        response = self.download('payload.html', b'<script>alert(document.cookie)</script>')

        self.assertIn('attachment', response['Content-Disposition'])
        self.assertNotIn('inline', response['Content-Disposition'])

    def test_svg_is_forced_to_attachment(self):
        """SVG подходит под префикс image/, но это активный документ:
        внутри работают <script> и обработчики событий, и открытый по
        ссылке он выполняется в домене портала."""
        response = self.download('payload.svg', b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')

        self.assertIn('attachment', response['Content-Disposition'])

    def test_pdf_and_images_still_open_inline(self):
        """Обратная проверка: запрет не должен задеть то, ради чего
        превью и делалось."""
        for name in ('doc.pdf', 'photo.png', 'photo.jpg', 'clip.mp4'):
            with self.subTest(file=name):
                response = self.download(name, b'binary payload for ' + name.encode())
                self.assertIn('inline', response['Content-Disposition'])

    def test_unknown_type_is_attachment_but_still_downloads(self):
        """Тип без MIME (.log, .bin) — не ошибка: файл просто скачивается,
        как и раньше."""
        response = self.download('server.log', b'log line')

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])

    def test_nosniff_is_set_by_django_not_only_by_nginx(self):
        """Заголовок ставит и nginx (deploy/nginx/portal.conf), но
        полагаться только на конфиг веб-сервера нельзя: в разработке
        nginx'а нет, а на проде он может быть развёрнут не из этого файла."""
        response = self.download('doc.pdf', b'pdf bytes')

        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')


class IsInlineSafeTest(SimpleTestCase):
    """Юнит-уровень того же правила — без БД и HTTP."""

    def test_active_formats_are_rejected(self):
        for mime in ('text/html', 'application/xhtml+xml', 'image/svg+xml',
                     'application/javascript', 'text/xml'):
            with self.subTest(mime=mime):
                self.assertFalse(is_inline_safe(mime))

    def test_passive_formats_are_allowed(self):
        for mime in ('application/pdf', 'image/png', 'image/jpeg', 'video/mp4',
                     'audio/mpeg', 'text/plain', 'text/csv'):
            with self.subTest(mime=mime):
                self.assertTrue(is_inline_safe(mime))

    def test_parameters_and_case_do_not_smuggle_a_type_through(self):
        """'text/html; charset=utf-8' и 'TEXT/HTML' — тот же text/html."""
        self.assertFalse(is_inline_safe('text/html; charset=utf-8'))
        self.assertFalse(is_inline_safe('TEXT/HTML'))
        self.assertFalse(is_inline_safe('  Image/SVG+XML  '))
        self.assertTrue(is_inline_safe('Application/PDF'))

    def test_empty_type_is_not_inline(self):
        self.assertFalse(is_inline_safe(''))
        self.assertFalse(is_inline_safe(None))
