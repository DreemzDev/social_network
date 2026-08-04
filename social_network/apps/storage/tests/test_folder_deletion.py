"""Удаление папки не должно быть единственным необратимым действием.

Раньше удаление подпапки обменника сносило файлы внутри физически: у
ExchangeFile.folder стоит on_delete=CASCADE, blob'ы освобождались
корректно (сигнал post_delete звал detach()), но пользователь не мог
ничего восстановить — при том что удаление тех же файлов по одному всегда
было обратимым через корзину (ARCHITECTURE.md, раздел 6). У каталога и
приватного доступа удаления папок из интерфейса не было вовсе, оставался
только /admin/ с тем же каскадом.

Теперь во всех трёх модулях содержимое поддерева сначала переезжает в
корзину, и только потом удаляется сама папка.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile, ExchangeFolder
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class FolderDeletionMovesContentToTrashTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='folder_del_user', password='pass12345')
        self.client.force_login(self.user)

    def _upload(self, category, name):
        uploaded = SimpleUploadedFile(name, name.encode() * 4, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=category)

    # --- обменник ---

    def test_deleting_exchange_subfolder_sends_files_to_trash(self):
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        exchange_file = ExchangeFile.objects.create(
            file_object=self._upload(FileObject.Category.EXCHANGE, 'in_folder.pdf'),
            owner=self.user, uploaded_by=self.user, folder=folder,
        )

        response = self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExchangeFolder.objects.filter(pk=folder.pk).exists())

        exchange_file.refresh_from_db()
        self.assertTrue(exchange_file.is_deleted)
        self.assertIsNone(exchange_file.folder_id)  # папки больше нет, файл в корне
        self.assertEqual(exchange_file.deleted_by, self.user)

    def test_deleting_exchange_subfolder_keeps_blob_active(self):
        """Файл ушёл в корзину, а не удалён — значит ссылка на FileObject
        жива и blob обязан остаться ACTIVE. Если бы файлы сносил каскад,
        blob уехал бы в ORPHAN и через STORAGE_ORPHAN_RETENTION_DAYS был
        бы стёрт с диска."""
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        file_object = self._upload(FileObject.Category.EXCHANGE, 'keep_active.pdf')
        ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user, folder=folder,
        )

        self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        blob = FileBlob.objects.get(pk=file_object.blob_id)
        self.assertEqual(blob.status, FileBlob.Status.ACTIVE)
        self.assertTrue(FileObject.objects.filter(pk=file_object.pk).exists())

    def test_deleting_exchange_folder_covers_nested_subfolders(self):
        parent = ExchangeFolder.objects.create(name='Родитель', owner=self.user, created_by=self.user)
        child = ExchangeFolder.objects.create(
            name='Вложенная', owner=self.user, parent=parent, created_by=self.user,
        )
        nested_file = ExchangeFile.objects.create(
            file_object=self._upload(FileObject.Category.EXCHANGE, 'nested.pdf'),
            owner=self.user, uploaded_by=self.user, folder=child,
        )

        self.client.post(reverse('exchange_folder_delete', args=[parent.pk]))

        nested_file.refresh_from_db()
        self.assertTrue(nested_file.is_deleted)

    def test_stranger_cannot_delete_exchange_subfolder(self):
        owner = User.objects.create_user(username='folder_del_owner', password='x')
        folder = ExchangeFolder.objects.create(name='Чужая', owner=owner, created_by=owner)

        response = self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ExchangeFolder.objects.filter(pk=folder.pk).exists())

    # --- каталог ---

    def test_deleting_catalog_folder_sends_documents_to_trash(self):
        folder = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        document = CatalogDocument.objects.create(
            file_object=self._upload(FileObject.Category.CATALOG, 'cat_in_folder.pdf'),
            folder=folder, title='Приказ', uploaded_by=self.user,
        )

        response = self.client.post(reverse('catalog_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CatalogFolder.objects.filter(pk=folder.pk).exists())

        document.refresh_from_db()
        self.assertTrue(document.is_deleted)

    # --- приватный доступ ---

    def test_deleting_dept_subfolder_moves_documents_to_parent_trash(self):
        """У приватного доступа документ вне папки не виден никому, так что
        отвязать его в None (как в обменнике и каталоге) нельзя — он
        исчез бы и из корзины. Документы переезжают к родителю."""
        parent = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        parent.allowed_users.add(self.user)
        child = DepartmentFolder.objects.create(name='Подпапка', parent=parent, created_by=self.user)
        child.allowed_users.add(self.user)

        document = DepartmentDocument.objects.create(
            file_object=self._upload(FileObject.Category.DOCUMENT, 'dept_in_folder.pdf'),
            folder=child, title='Документ', uploaded_by=self.user,
        )

        response = self.client.post(reverse('deptdocs_folder_delete', args=[child.pk]))

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertTrue(document.is_deleted)
        self.assertEqual(document.folder_id, parent.pk)

    def test_deleted_dept_document_stays_visible_in_trash(self):
        """Смысл переноса к родителю: корзина фильтруется по
        folder__allowed_users, и документ с folder=None выпал бы из неё."""
        parent = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        parent.allowed_users.add(self.user)
        child = DepartmentFolder.objects.create(name='Подпапка', parent=parent, created_by=self.user)
        child.allowed_users.add(self.user)

        document = DepartmentDocument.objects.create(
            file_object=self._upload(FileObject.Category.DOCUMENT, 'dept_visible.pdf'),
            folder=child, title='Документ', uploaded_by=self.user,
        )

        self.client.post(reverse('deptdocs_folder_delete', args=[child.pk]))

        response = self.client.get(reverse('deptdocs_trash'))
        self.assertIn(document, response.context['documents'])

    def test_deleting_root_dept_folder_with_documents_is_refused(self):
        """Родителя нет — переносить документы некуда. Молча стереть их
        навсегда нельзя, поэтому удаление отклоняется с объяснением."""
        folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        folder.allowed_users.add(self.user)
        document = DepartmentDocument.objects.create(
            file_object=self._upload(FileObject.Category.DOCUMENT, 'dept_root.pdf'),
            folder=folder, title='Документ', uploaded_by=self.user,
        )

        response = self.client.post(reverse('deptdocs_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertIn('перенесите', response.json()['error'].lower())
        self.assertTrue(DepartmentFolder.objects.filter(pk=folder.pk).exists())
        self.assertTrue(DepartmentDocument.objects.filter(pk=document.pk).exists())

    def test_empty_root_dept_folder_can_be_deleted(self):
        folder = DepartmentFolder.objects.create(name='Пустой отдел', created_by=self.user)
        folder.allowed_users.add(self.user)

        response = self.client.post(reverse('deptdocs_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DepartmentFolder.objects.filter(pk=folder.pk).exists())

    def test_user_without_access_cannot_delete_dept_folder(self):
        other = User.objects.create_user(username='folder_del_other', password='x')
        folder = DepartmentFolder.objects.create(name='Чужой отдел', created_by=other)
        folder.allowed_users.add(other)

        response = self.client.post(reverse('deptdocs_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(DepartmentFolder.objects.filter(pk=folder.pk).exists())
