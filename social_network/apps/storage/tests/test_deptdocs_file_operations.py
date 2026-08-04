"""Переименование, перемещение, пересылка и массовое удаление в приватном
доступе — тот же набор, что и в каталоге (test_catalog_file_operations.py),
но с проверкой allowed_users на каждом шаге: доступ к папке требуется и на
операцию с документом, и на саму папку назначения при переносе."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class DeptdocsRenameTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='dept_rename_user', password='pass12345')
        self.outsider = User.objects.create_user(username='dept_rename_outsider', password='pass12345')
        self.folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        self.folder.allowed_users.add(self.user)

    def _create_document(self, name='doc.pdf', content=b'content'):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        return DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title=name, uploaded_by=self.user,
        )

    def test_member_can_rename_document(self):
        document = self._create_document()
        self.client.force_login(self.user)

        response = self.client.post(reverse('deptdocs_rename', args=[document.pk]), {'title': 'Новое название'})

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.title, 'Новое название')

    def test_outsider_cannot_rename_document(self):
        document = self._create_document()
        self.client.force_login(self.outsider)

        response = self.client.post(reverse('deptdocs_rename', args=[document.pk]), {'title': 'Взлом'})

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertNotEqual(document.title, 'Взлом')

    def test_member_can_rename_folder(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('deptdocs_folder_rename', args=[self.folder.pk]), {'name': 'Новый отдел'},
        )

        self.assertEqual(response.status_code, 200)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, 'Новый отдел')

    def test_outsider_cannot_rename_folder(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            reverse('deptdocs_folder_rename', args=[self.folder.pk]), {'name': 'Взлом'},
        )

        self.assertEqual(response.status_code, 403)


class DeptdocsMoveTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='dept_move_user', password='pass12345')
        self.outsider = User.objects.create_user(username='dept_move_outsider', password='pass12345')
        self.source = DepartmentFolder.objects.create(name='Источник', created_by=self.user)
        self.source.allowed_users.add(self.user)
        self.target = DepartmentFolder.objects.create(name='Назначение', created_by=self.user)
        self.target.allowed_users.add(self.user)
        self.client.force_login(self.user)

    def test_move_document_to_accessible_folder(self):
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        document = DepartmentDocument.objects.create(
            folder=self.source, file_object=file_object, title='Документ', uploaded_by=self.user,
        )

        response = self.client.post(reverse('deptdocs_move', args=[document.pk]), {'folder_id': self.target.pk})

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.target.pk)

    def test_move_document_to_inaccessible_folder_is_forbidden(self):
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        document = DepartmentDocument.objects.create(
            folder=self.source, file_object=file_object, title='Документ', uploaded_by=self.user,
        )
        no_access_folder = DepartmentFolder.objects.create(name='Чужая', created_by=self.outsider)
        no_access_folder.allowed_users.add(self.outsider)

        response = self.client.post(
            reverse('deptdocs_move', args=[document.pk]), {'folder_id': no_access_folder.pk},
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.source.pk)

    def test_move_folder_rejects_moving_into_itself(self):
        response = self.client.post(
            reverse('deptdocs_folder_move', args=[self.source.pk]), {'parent_id': self.source.pk},
        )
        self.assertEqual(response.status_code, 400)

    def test_move_folder_to_inaccessible_parent_is_forbidden(self):
        no_access_folder = DepartmentFolder.objects.create(name='Чужая', created_by=self.outsider)
        no_access_folder.allowed_users.add(self.outsider)

        response = self.client.post(
            reverse('deptdocs_folder_move', args=[self.source.pk]), {'parent_id': no_access_folder.pk},
        )

        self.assertEqual(response.status_code, 403)


class DeptdocsBulkTrashTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='dept_bulk_user', password='pass12345')
        self.folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        self.folder.allowed_users.add(self.user)
        self.client.force_login(self.user)

    def _create_document(self, name, content):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        return DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title=name, uploaded_by=self.user,
        )

    def test_bulk_trash_accessible_documents(self):
        """CELERY_TASK_ALWAYS_EAGER=True под manage.py test (settings.py)
        делает .delay() синхронным — задача отрабатывает до ответа."""
        doc1 = self._create_document('a.pdf', b'a content')
        doc2 = self._create_document('b.pdf', b'b content')

        response = self.client.post(reverse('deptdocs_bulk_trash'), {'doc_ids': [doc1.pk, doc2.pk]})

        self.assertIn('task_id', response.json())
        doc1.refresh_from_db()
        doc2.refresh_from_db()
        self.assertTrue(doc1.is_deleted)
        self.assertTrue(doc2.is_deleted)

    def test_bulk_trash_ignores_inaccessible_documents(self):
        """Документ из недоступной папки в списке id молча игнорируется —
        не 403, чтобы не подтверждать существование чужого id."""
        stranger = User.objects.create_user(username='dept_bulk_stranger', password='x')
        foreign_folder = DepartmentFolder.objects.create(name='Чужая', created_by=stranger)
        foreign_folder.allowed_users.add(stranger)
        uploaded = SimpleUploadedFile('foreign.pdf', b'foreign content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=stranger, category=FileObject.Category.DOCUMENT)
        foreign_doc = DepartmentDocument.objects.create(
            folder=foreign_folder, file_object=file_object, title='foreign.pdf', uploaded_by=stranger,
        )

        response = self.client.post(reverse('deptdocs_bulk_trash'), {'doc_ids': [foreign_doc.pk]})

        self.assertEqual(response.status_code, 200)
        foreign_doc.refresh_from_db()
        self.assertFalse(foreign_doc.is_deleted)


class DeptdocsSendToExchangeTest(TestCase):

    def setUp(self):
        self.sender = User.objects.create_user(username='dept_send_sender', password='pass12345')
        self.recipient = User.objects.create_user(username='dept_send_recipient', password='pass12345')
        self.folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.sender)
        self.folder.allowed_users.add(self.sender)
        self.client.force_login(self.sender)

    def test_sending_creates_exchange_copy(self):
        uploaded = SimpleUploadedFile('order.pdf', b'order content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.sender, category=FileObject.Category.DOCUMENT)
        document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Приказ', uploaded_by=self.sender,
        )

        response = self.client.post(
            reverse('deptdocs_send_to_exchange', args=[document.pk]), {'recipient_id': self.recipient.pk},
        )

        self.assertEqual(response.status_code, 200)
        exchange_file = ExchangeFile.objects.filter(owner=self.recipient).first()
        self.assertIsNotNone(exchange_file)
        self.assertEqual(exchange_file.file_object.blob_id, file_object.blob_id)
