"""?inline=1 на download-эндпоинтах трёх модулей — открыть файл в браузере
(PDF/картинку) вместо скачивания. StorageService.get_download_response уже
поддерживал inline=True (используется для phonebook), здесь просто
проброшен query-параметр в тех же вьюхах, права те же, что и на обычное
скачивание."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService

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
