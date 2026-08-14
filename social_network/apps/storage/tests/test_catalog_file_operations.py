"""Переименование, перемещение, пересылка и массовые операции в каталоге.

До этого набора функций у catalog были только upload/download/trash —
загрузили не под тем именем или не в ту папку, и оставалось только удалить
и перезалить заново."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from exchange.models import ExchangeFile
from profiles.models import Notification
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class CatalogRenameTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='cat_rename_user', password='pass12345')
        self.client.force_login(self.user)

    def test_rename_document(self):
        uploaded = SimpleUploadedFile('old.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Старое имя', uploaded_by=self.user,
        )

        response = self.client.post(reverse('catalog_rename', args=[document.pk]), {'title': 'Новое имя'})

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.title, 'Новое имя')

    def test_rename_requires_non_empty_title(self):
        uploaded = SimpleUploadedFile('x.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(file_object=file_object, title='Было', uploaded_by=self.user)

        response = self.client.post(reverse('catalog_rename', args=[document.pk]), {'title': '  '})

        self.assertEqual(response.status_code, 400)
        document.refresh_from_db()
        self.assertEqual(document.title, 'Было')

    def test_rename_folder(self):
        folder = CatalogFolder.objects.create(name='Старая папка', created_by=self.user)

        response = self.client.post(reverse('catalog_folder_rename', args=[folder.pk]), {'name': 'Новая папка'})

        self.assertEqual(response.status_code, 200)
        folder.refresh_from_db()
        self.assertEqual(folder.name, 'Новая папка')


class CatalogMoveTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='cat_move_user', password='pass12345')
        self.client.force_login(self.user)

    def test_move_document_into_folder(self):
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(file_object=file_object, title='Документ', uploaded_by=self.user)
        target = CatalogFolder.objects.create(name='Целевая папка', created_by=self.user)

        response = self.client.post(reverse('catalog_move', args=[document.pk]), {'folder_id': target.pk})

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, target.pk)

    def test_move_document_to_root(self):
        source = CatalogFolder.objects.create(name='Исходная', created_by=self.user)
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            folder=source, file_object=file_object, title='Документ', uploaded_by=self.user,
        )

        response = self.client.post(reverse('catalog_move', args=[document.pk]), {'folder_id': ''})

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)

    def test_move_folder_rejects_moving_into_itself(self):
        folder = CatalogFolder.objects.create(name='Папка', created_by=self.user)

        response = self.client.post(reverse('catalog_folder_move', args=[folder.pk]), {'parent_id': folder.pk})

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_move_folder_rejects_moving_into_own_descendant(self):
        """Перенос папки в собственного потомка создал бы цикл в дереве
        self-FK — обход subfolders зациклился бы."""
        root = CatalogFolder.objects.create(name='Корень', created_by=self.user)
        child = CatalogFolder.objects.create(name='Ребёнок', parent=root, created_by=self.user)
        grandchild = CatalogFolder.objects.create(name='Внук', parent=child, created_by=self.user)

        response = self.client.post(reverse('catalog_folder_move', args=[root.pk]), {'parent_id': grandchild.pk})

        self.assertEqual(response.status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)

    def test_move_folder_to_valid_new_parent_succeeds(self):
        folder = CatalogFolder.objects.create(name='Перемещаемая', created_by=self.user)
        new_parent = CatalogFolder.objects.create(name='Новый родитель', created_by=self.user)

        response = self.client.post(reverse('catalog_folder_move', args=[folder.pk]), {'parent_id': new_parent.pk})

        self.assertEqual(response.status_code, 200)
        folder.refresh_from_db()
        self.assertEqual(folder.parent_id, new_parent.pk)


class CatalogBulkOperationsTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='cat_bulk_user', password='pass12345')
        self.client.force_login(self.user)

    def _create_document(self, name, content):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        return CatalogDocument.objects.create(file_object=file_object, title=name, uploaded_by=self.user)

    def test_bulk_move(self):
        """Bulk-операции уходят в Celery (CELERY_TASK_ALWAYS_EAGER=True под
        manage.py test делает .delay() синхронным — см. settings.py), поэтому
        к моменту ответа задача уже отработала и данные в БД видны сразу,
        хотя эндпоинт возвращает только task_id, а не готовый результат."""
        doc1 = self._create_document('a.pdf', b'a content')
        doc2 = self._create_document('b.pdf', b'b content')
        target = CatalogFolder.objects.create(name='Куда переносим', created_by=self.user)

        response = self.client.post(
            reverse('catalog_bulk_move'), {'doc_ids': [doc1.pk, doc2.pk], 'folder_id': target.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('task_id', response.json())
        doc1.refresh_from_db()
        doc2.refresh_from_db()
        self.assertEqual(doc1.folder_id, target.pk)
        self.assertEqual(doc2.folder_id, target.pk)

    def test_bulk_trash(self):
        doc1 = self._create_document('c.pdf', b'c content')
        doc2 = self._create_document('d.pdf', b'd content')

        response = self.client.post(reverse('catalog_bulk_trash'), {'doc_ids': [doc1.pk, doc2.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertIn('task_id', response.json())
        doc1.refresh_from_db()
        doc2.refresh_from_db()
        self.assertTrue(doc1.is_deleted)
        self.assertTrue(doc2.is_deleted)

    def test_bulk_move_requires_selection(self):
        response = self.client.post(reverse('catalog_bulk_move'), {'doc_ids': []})
        self.assertEqual(response.status_code, 400)


class CatalogSendToExchangeTest(TestCase):

    def setUp(self):
        self.sender = User.objects.create_user(username='send_sender', password='pass12345')
        self.recipient = User.objects.create_user(username='send_recipient', password='pass12345')
        self.client.force_login(self.sender)

    def test_sending_document_creates_exchange_file_on_same_blob(self):
        uploaded = SimpleUploadedFile('order.pdf', b'order content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.sender, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Приказ', uploaded_by=self.sender,
        )

        response = self.client.post(
            reverse('catalog_send_to_exchange', args=[document.pk]), {'recipient_id': self.recipient.pk},
        )

        self.assertEqual(response.status_code, 200)
        exchange_file = ExchangeFile.objects.filter(owner=self.recipient).first()
        self.assertIsNotNone(exchange_file)
        self.assertEqual(exchange_file.file_object.blob_id, file_object.blob_id)
        self.assertNotEqual(exchange_file.file_object_id, file_object.pk)

    def test_sending_document_notifies_recipient(self):
        uploaded = SimpleUploadedFile('order2.pdf', b'order content 2', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.sender, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Приказ 2', uploaded_by=self.sender,
        )

        self.client.post(
            reverse('catalog_send_to_exchange', args=[document.pk]), {'recipient_id': self.recipient.pk},
        )

        self.assertTrue(
            Notification.objects.filter(recipient=self.recipient, kind=Notification.Kind.FILE_SHARED).exists()
        )

    def test_sending_to_self_does_not_notify(self):
        uploaded = SimpleUploadedFile('order3.pdf', b'order content 3', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.sender, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Приказ 3', uploaded_by=self.sender,
        )

        self.client.post(
            reverse('catalog_send_to_exchange', args=[document.pk]), {'recipient_id': self.sender.pk},
        )

        self.assertFalse(
            Notification.objects.filter(recipient=self.sender, kind=Notification.Kind.FILE_SHARED).exists()
        )

    def test_deleting_original_document_does_not_affect_sent_copy(self):
        uploaded = SimpleUploadedFile('order4.pdf', b'order content 4', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.sender, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Приказ 4', uploaded_by=self.sender,
        )

        self.client.post(
            reverse('catalog_send_to_exchange', args=[document.pk]), {'recipient_id': self.recipient.pk},
        )
        exchange_file = ExchangeFile.objects.get(owner=self.recipient)
        copied_blob_id = exchange_file.file_object.blob_id

        document.is_deleted = True
        document.save(update_fields=['is_deleted'])
        from storage.signals import attribute_deletion
        attribute_deletion(document, user=self.sender, consumer='catalog.CatalogDocument')
        document.delete()

        self.assertTrue(ExchangeFile.objects.filter(pk=exchange_file.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=copied_blob_id).status, FileBlob.Status.ACTIVE)


class CatalogIsOpenToEveryoneTest(TestCase):
    """Каталог открыт на запись всем — это решение, а не пропуск.

    Подтверждено владельцем портала 14.08.2026 (ARCHITECTURE.md, «Каталог
    открыт на запись»). Тест стоит здесь потому, что отсутствие проверок
    выглядит дырой при каждом аудите: пусть попытка «починить» роняет
    прогон, а не уезжает в прод молча. Заодно фиксируется отличие от
    обменника, где то же действие постороннему запрещено.
    """

    def setUp(self):
        self.owner = User.objects.create_user(username='cat_open_owner', password='pass12345')
        self.stranger = User.objects.create_user(username='cat_open_stranger', password='pass12345')
        self.client.force_login(self.stranger)

    def document(self, title='Приказ'):
        uploaded = SimpleUploadedFile('doc.pdf', b'catalog content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.CATALOG)
        return CatalogDocument.objects.create(
            file_object=file_object, title=title, uploaded_by=self.owner,
        )

    def test_stranger_renames_and_trashes_a_foreign_document(self):
        document = self.document()

        renamed = self.client.post(reverse('catalog_rename', args=[document.pk]), {'title': 'Чужая правка'})
        trashed = self.client.post(reverse('catalog_trash_doc', args=[document.pk]))

        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(trashed.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.title, 'Чужая правка')
        self.assertTrue(document.is_deleted)

    def test_stranger_purges_a_foreign_document(self):
        """Самое сильное следствие: удаление окончательное и необратимое."""
        document = self.document('На удаление')
        document.is_deleted = True
        document.save(update_fields=['is_deleted'])

        response = self.client.post(reverse('catalog_purge', args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CatalogDocument.objects.filter(pk=document.pk).exists())

    def test_exchange_does_not_work_this_way(self):
        """Граница решения: в обменнике посторонний удалить не может."""
        uploaded = SimpleUploadedFile('mine.pdf', b'exchange content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.owner, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(
            file_object=file_object, owner=self.owner, uploaded_by=self.owner,
        )

        response = self.client.post(reverse('exchange_trash_file', args=[exchange_file.pk]))

        self.assertEqual(response.status_code, 403)
        exchange_file.refresh_from_db()
        self.assertFalse(exchange_file.is_deleted)
