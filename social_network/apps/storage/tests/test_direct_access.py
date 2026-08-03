"""Регрессионный тест: файлы storage недоступны по прямой ссылке.

Повод: blob'ы физически лежат в MEDIA_ROOT/storage/blobs/, а корневой
urls.py раздавал весь MEDIA_ROOT через static(). Путь предсказуем
(/media/storage/blobs/<checksum>), поэтому приватный документ отдела
скачивался по прямой ссылке даже анонимом — вся проверка прав обходилась.
"""

import os

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from deptdocs.models import DepartmentDocument, DepartmentFolder
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class DirectMediaAccessTest(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user(username='direct_staff', password='x', is_staff=True)
        self.outsider = User.objects.create_user(username='direct_outsider', password='x')

        self.folder = DepartmentFolder.objects.create(name='Приватная папка', created_by=self.staff)
        self.folder.allowed_users.set([self.staff])

        uploaded = SimpleUploadedFile('secret.pdf', b'SECRET CONTENT', content_type='application/pdf')
        file_object = StorageService.upload(
            uploaded, user=self.staff, category=FileObject.Category.DOCUMENT
        )
        self.document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Секретный документ', uploaded_by=self.staff,
        )

        self.blob_url = '/media/' + file_object.blob.file.name.replace(os.sep, '/')

    def test_anonymous_cannot_fetch_blob_directly(self):
        response = Client().get(self.blob_url)
        self.assertNotEqual(
            response.status_code, 200,
            'Аноним скачал приватный файл по прямой ссылке на /media/ в обход прав',
        )

    def test_user_without_folder_access_cannot_fetch_blob_directly(self):
        client = Client()
        client.force_login(self.outsider)

        response = client.get(self.blob_url)
        self.assertNotEqual(
            response.status_code, 200,
            'Пользователь без доступа к папке скачал файл по прямой ссылке в обход проверки прав',
        )

    def test_authorised_user_still_downloads_through_the_view(self):
        client = Client()
        client.force_login(self.staff)

        response = client.get(f'/deptdocs/download/{self.document.pk}/')
        self.assertEqual(
            response.status_code, 200,
            'Штатный путь скачивания сломан — запрет прямого доступа задел легальный сценарий',
        )
