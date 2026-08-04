"""Переименование, перемещение между подпапками, пересылка в каталог и
массовое удаление в обменнике. Тот же набор функций, что и в
test_catalog_file_operations.py / test_deptdocs_file_operations.py, но с
правами обменника: can_be_deleted_by (владелец папки или тот, кто
загрузил/создал), а не allowed_users."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from exchange.models import ExchangeFile, ExchangeFolder
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class ExchangeRenameTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='exch_ren_owner', password='pass12345')
        self.stranger = User.objects.create_user(username='exch_ren_stranger', password='pass12345')

    def _upload_file(self, name='file.pdf', content=b'content'):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        return ExchangeFile.objects.create(file_object=file_object, owner=self.owner, uploaded_by=self.owner)

    def test_owner_can_rename_file(self):
        exchange_file = self._upload_file()
        self.client.force_login(self.owner)

        response = self.client.post(reverse('exchange_rename', args=[exchange_file.pk]), {'name': 'Новое имя.pdf'})

        self.assertEqual(response.status_code, 200)
        exchange_file.file_object.refresh_from_db()
        self.assertEqual(exchange_file.file_object.original_name, 'Новое имя.pdf')

    def test_third_party_cannot_rename_file(self):
        exchange_file = self._upload_file()
        third_party = User.objects.create_user(username='exch_ren_third', password='x')
        self.client.force_login(third_party)

        response = self.client.post(reverse('exchange_rename', args=[exchange_file.pk]), {'name': 'Взлом.pdf'})

        self.assertEqual(response.status_code, 403)

    def test_owner_can_rename_folder(self):
        folder = ExchangeFolder.objects.create(name='Старое', owner=self.owner, created_by=self.owner)
        self.client.force_login(self.owner)

        response = self.client.post(reverse('exchange_folder_rename', args=[folder.pk]), {'name': 'Новое'})

        self.assertEqual(response.status_code, 200)
        folder.refresh_from_db()
        self.assertEqual(folder.name, 'Новое')


class ExchangeMoveTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='exch_move_owner', password='pass12345')
        self.client.force_login(self.owner)

    def test_move_file_between_subfolders(self):
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(file_object=file_object, owner=self.owner, uploaded_by=self.owner)
        target = ExchangeFolder.objects.create(name='Проект', owner=self.owner, created_by=self.owner)

        response = self.client.post(reverse('exchange_move', args=[exchange_file.pk]), {'folder_id': target.pk})

        self.assertEqual(response.status_code, 200)
        exchange_file.refresh_from_db()
        self.assertEqual(exchange_file.folder_id, target.pk)

    def test_cannot_move_file_into_someone_elses_subfolder(self):
        """folder_id должен принадлежать owner'у файла — иначе файл из
        одной личной папки оказался бы в подпапке другой."""
        stranger = User.objects.create_user(username='exch_move_stranger', password='x')
        foreign_folder = ExchangeFolder.objects.create(name='Чужая', owner=stranger, created_by=stranger)

        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(file_object=file_object, owner=self.owner, uploaded_by=self.owner)

        response = self.client.post(
            reverse('exchange_move', args=[exchange_file.pk]), {'folder_id': foreign_folder.pk},
        )

        self.assertEqual(response.status_code, 404)

    def test_third_party_cannot_move_file(self):
        third_party = User.objects.create_user(username='exch_move_third', password='x')
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(file_object=file_object, owner=self.owner, uploaded_by=self.owner)
        self.client.force_login(third_party)

        response = self.client.post(reverse('exchange_move', args=[exchange_file.pk]), {'folder_id': ''})

        self.assertEqual(response.status_code, 403)


class ExchangeBulkTrashTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='exch_bulk_owner', password='pass12345')
        self.client.force_login(self.owner)

    def _upload_file(self, name, content):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        return ExchangeFile.objects.create(file_object=file_object, owner=self.owner, uploaded_by=self.owner)

    def test_bulk_trash_own_files(self):
        """CELERY_TASK_ALWAYS_EAGER=True под manage.py test (settings.py)
        делает .delay() синхронным — задача отрабатывает до ответа."""
        file1 = self._upload_file('a.pdf', b'a content')
        file2 = self._upload_file('b.pdf', b'b content')

        response = self.client.post(reverse('exchange_bulk_trash'), {'file_ids': [file1.pk, file2.pk]})

        self.assertIn('task_id', response.json())
        file1.refresh_from_db()
        file2.refresh_from_db()
        self.assertTrue(file1.is_deleted)
        self.assertTrue(file2.is_deleted)

    def test_bulk_trash_ignores_files_without_permission(self):
        stranger = User.objects.create_user(username='exch_bulk_stranger', password='x')
        uploaded = SimpleUploadedFile('foreign.pdf', b'foreign content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=stranger, category=FileObject.Category.EXCHANGE)
        foreign_file = ExchangeFile.objects.create(file_object=file_object, owner=stranger, uploaded_by=stranger)

        response = self.client.post(reverse('exchange_bulk_trash'), {'file_ids': [foreign_file.pk]})

        self.assertEqual(response.status_code, 200)
        foreign_file.refresh_from_db()
        self.assertFalse(foreign_file.is_deleted)


class ExchangeSendToCatalogTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='exch_send_user', password='pass12345')
        self.client.force_login(self.user)

    def test_sending_file_creates_catalog_document_on_same_blob(self):
        uploaded = SimpleUploadedFile('order.pdf', b'order content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(file_object=file_object, owner=self.user, uploaded_by=self.user)

        response = self.client.post(reverse('exchange_send_to_catalog', args=[exchange_file.pk]))

        self.assertEqual(response.status_code, 200)
        document = CatalogDocument.objects.filter(uploaded_by=self.user).first()
        self.assertIsNotNone(document)
        self.assertEqual(document.file_object.blob_id, file_object.blob_id)

    def test_anyone_can_send_since_exchange_is_open(self):
        """Обменник открыт всем — переслать в каталог может не только
        владелец, но и любой сотрудник, кто видит файл."""
        owner = User.objects.create_user(username='exch_send_owner', password='x')
        uploaded = SimpleUploadedFile('shared.pdf', b'shared content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=owner, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(file_object=file_object, owner=owner, uploaded_by=owner)

        response = self.client.post(reverse('exchange_send_to_catalog', args=[exchange_file.pk]))

        self.assertEqual(response.status_code, 200)
