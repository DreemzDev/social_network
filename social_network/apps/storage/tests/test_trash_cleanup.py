"""Автоочистка корзины по сроку (ARCHITECTURE.md, раздел 6.1).

До этой задачи корзина catalog и deptdocs чистилась только вручную по
одному файлу — запись с is_deleted=True лежала в БД неограниченно. Здесь
проверяется, что просроченная запись в корзине действительно уходит и
освобождает blob, а свежая — остаётся нетронутой.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from catalog.models import CatalogDocument, CatalogFolder
from catalog.tasks import cleanup_catalog_trash
from deptdocs.models import DepartmentDocument, DepartmentFolder
from deptdocs.tasks import cleanup_deptdocs_trash
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class TrashCleanupTestMixin:

    def _upload(self, category, name='trash_cleanup.pdf', content=None):
        content = content or name.encode()
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=category)


class CatalogTrashCleanupTest(TrashCleanupTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='catalog_cleanup', password='x')
        self.folder = CatalogFolder.objects.create(name='Приказы', created_by=self.user)

    def test_expired_trash_entry_is_purged_and_blob_orphaned(self):
        file_object = self._upload(FileObject.Category.CATALOG, name='expired.pdf')
        blob_id = file_object.blob_id
        document = CatalogDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Просроченный',
            uploaded_by=self.user, is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=31),
        )

        purged = cleanup_catalog_trash()

        self.assertEqual(purged, 1)
        self.assertFalse(CatalogDocument.objects.filter(pk=document.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)

    def test_fresh_trash_entry_is_not_touched(self):
        file_object = self._upload(FileObject.Category.CATALOG, name='fresh.pdf')
        document = CatalogDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Свежий',
            uploaded_by=self.user, is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=5),
        )

        purged = cleanup_catalog_trash()

        self.assertEqual(purged, 0)
        self.assertTrue(CatalogDocument.objects.filter(pk=document.pk).exists())

    def test_document_not_in_trash_is_never_touched(self):
        file_object = self._upload(FileObject.Category.CATALOG, name='active.pdf')
        document = CatalogDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Активный', uploaded_by=self.user,
        )

        purged = cleanup_catalog_trash()

        self.assertEqual(purged, 0)
        self.assertTrue(CatalogDocument.objects.filter(pk=document.pk).exists())


class DeptdocsTrashCleanupTest(TrashCleanupTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='deptdocs_cleanup', password='x')
        self.folder = DepartmentFolder.objects.create(name='Отдел кадров', created_by=self.user)
        self.folder.allowed_users.add(self.user)

    def test_expired_trash_entry_is_purged_and_blob_orphaned(self):
        file_object = self._upload(FileObject.Category.DOCUMENT, name='expired.pdf')
        blob_id = file_object.blob_id
        document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Просроченный',
            uploaded_by=self.user, is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=31),
        )

        purged = cleanup_deptdocs_trash()

        self.assertEqual(purged, 1)
        self.assertFalse(DepartmentDocument.objects.filter(pk=document.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)

    def test_fresh_trash_entry_is_not_touched(self):
        file_object = self._upload(FileObject.Category.DOCUMENT, name='fresh.pdf')
        document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Свежий',
            uploaded_by=self.user, is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=5),
        )

        purged = cleanup_deptdocs_trash()

        self.assertEqual(purged, 0)
        self.assertTrue(DepartmentDocument.objects.filter(pk=document.pk).exists())
