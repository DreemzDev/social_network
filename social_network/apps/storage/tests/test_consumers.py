"""Обязательный по чек-листу тест для каждого потребителя storage.

ARCHITECTURE.md, раздел 10, пункт 3 требует от каждого модуля-потребителя
теста «окончательное удаление объекта X переводит blob без других ссылок в
ORPHAN». Тесты собраны здесь, а не в приложениях-потребителях, потому что
проверяют они инвариант storage, а не бизнес-логику потребителя: при
изменении правил жизненного цикла blob'а чинить придётся именно storage, и
удобнее, когда все такие тесты падают в одном месте.

Проверяется реальный путь окончательного удаления — через view, а не через
ORM напрямую: именно во view живёт связка «удалить свою запись + снять
ссылку», и именно её легко сломать.

Заодно проверяется атрибуция в StorageAuditLog: detach() выполняет сигнал
post_delete, который сам не знает инициатора, поэтому view обязана пометить
запись через attribute_deletion(). Без пометки журнал ответит «удалил
никто», а для портала с приказами это требование, а не пожелание
(ARCHITECTURE.md, раздел 2).
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile
from phonebook.models import Phonebook
from posts.models import Post, PostFile
from storage.models import FileBlob, FileObject, StorageAuditLog
from storage.services import StorageService

User = get_user_model()


class ConsumerPurgeTestMixin:
    """Общие проверки: blob осиротел и в журнале есть кто это сделал."""

    def _upload(self, category, name='consumer.pdf', content=None, user=None):
        content = content or f'content for {category}'.encode()
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=user or self.user, category=category)

    def assertBlobOrphaned(self, blob_id):
        blob = FileBlob.objects.get(pk=blob_id)
        self.assertEqual(
            blob.status, FileBlob.Status.ORPHAN,
            'Окончательное удаление записи потребителя не перевело blob в ORPHAN — '
            'файл останется на диске навсегда.',
        )
        self.assertIsNotNone(blob.orphaned_at, 'orphaned_at не проставлен — purge не найдёт blob')

    def assertDetachAttributedTo(self, checksum, user, consumer):
        entry = StorageAuditLog.objects.filter(
            action=StorageAuditLog.Action.DETACH, checksum=checksum,
        ).order_by('-created_at').first()

        self.assertIsNotNone(entry, 'В журнале нет записи DETACH')
        self.assertEqual(
            entry.user, user,
            'В журнале не сохранился инициатор удаления — view не пометила запись '
            'через attribute_deletion() перед delete().',
        )
        self.assertEqual(entry.consumer, consumer)


class ExchangeFilePurgeTest(ConsumerPurgeTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='exch_owner', password='pass12345')
        self.client.force_login(self.user)

    def test_purge_from_trash_orphans_blob(self):
        file_object = self._upload(FileObject.Category.EXCHANGE)
        blob_id = file_object.blob_id
        checksum = file_object.blob.checksum
        exchange_file = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user, is_deleted=True,
        )

        response = self.client.post(reverse('exchange_purge', args=[exchange_file.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExchangeFile.objects.filter(pk=exchange_file.pk).exists())
        self.assertBlobOrphaned(blob_id)
        self.assertDetachAttributedTo(checksum, self.user, 'exchange.ExchangeFile')

    def test_trashed_file_keeps_blob_active(self):
        """Корзина не трогает storage: пока запись жива, blob обязан
        остаться ACTIVE, иначе восстановление вернёт битую ссылку."""
        file_object = self._upload(FileObject.Category.EXCHANGE, name='trashed.pdf')
        blob_id = file_object.blob_id
        exchange_file = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user,
        )

        self.client.post(reverse('exchange_trash_file', args=[exchange_file.pk]))

        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ACTIVE)

    def test_stranger_cannot_purge_someone_elses_file(self):
        stranger = User.objects.create_user(username='exch_stranger', password='pass12345')
        owner = User.objects.create_user(username='exch_other', password='pass12345')
        file_object = self._upload(FileObject.Category.EXCHANGE, name='foreign.pdf')
        exchange_file = ExchangeFile.objects.create(
            file_object=file_object, owner=owner, uploaded_by=owner, is_deleted=True,
        )
        self.client.force_login(stranger)

        response = self.client.post(reverse('exchange_purge', args=[exchange_file.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ExchangeFile.objects.filter(pk=exchange_file.pk).exists())


class CatalogDocumentPurgeTest(ConsumerPurgeTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='cat_user', password='pass12345')
        self.client.force_login(self.user)

    def test_purge_from_trash_orphans_blob(self):
        file_object = self._upload(FileObject.Category.CATALOG)
        blob_id = file_object.blob_id
        checksum = file_object.blob.checksum
        folder = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        document = CatalogDocument.objects.create(
            folder=folder, file_object=file_object, title='Приказ №1',
            uploaded_by=self.user, is_deleted=True,
        )

        response = self.client.post(reverse('catalog_purge', args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CatalogDocument.objects.filter(pk=document.pk).exists())
        self.assertBlobOrphaned(blob_id)
        self.assertDetachAttributedTo(checksum, self.user, 'catalog.CatalogDocument')


class DepartmentDocumentPurgeTest(ConsumerPurgeTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='dept_user', password='pass12345')
        self.folder = DepartmentFolder.objects.create(name='Отдел кадров', created_by=self.user)
        self.folder.allowed_users.add(self.user)
        self.client.force_login(self.user)

    def test_purge_from_trash_orphans_blob(self):
        file_object = self._upload(FileObject.Category.DOCUMENT)
        blob_id = file_object.blob_id
        checksum = file_object.blob.checksum
        document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Штатное расписание',
            uploaded_by=self.user, is_deleted=True,
        )

        response = self.client.post(reverse('deptdocs_purge', args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertBlobOrphaned(blob_id)
        self.assertDetachAttributedTo(checksum, self.user, 'deptdocs.DepartmentDocument')

    def test_user_without_folder_access_cannot_purge(self):
        outsider = User.objects.create_user(username='dept_outsider', password='pass12345')
        file_object = self._upload(FileObject.Category.DOCUMENT, name='secret.pdf')
        document = DepartmentDocument.objects.create(
            folder=self.folder, file_object=file_object, title='Секретное',
            uploaded_by=self.user, is_deleted=True,
        )
        self.client.force_login(outsider)

        response = self.client.post(reverse('deptdocs_purge', args=[document.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(DepartmentDocument.objects.filter(pk=document.pk).exists())


class PostFilePurgeTest(ConsumerPurgeTestMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='post_author', password='pass12345')

    def test_deleting_post_file_orphans_blob(self):
        file_object = self._upload(FileObject.Category.DOCUMENT, name='attachment.pdf')
        blob_id = file_object.blob_id
        post = Post.objects.create(author=self.user, content='Пост с вложением')
        post_file = PostFile.objects.create(post=post, file_object=file_object)

        post_file.delete()

        self.assertBlobOrphaned(blob_id)

    def test_deleting_post_orphans_blobs_of_its_attachments(self):
        """Каскад Post → PostFile. Раньше здесь была реальная утечка:
        PostDeleteView переопределял delete(), который в Django >= 4.0 на
        POST не вызывается (ARCHITECTURE.md, раздел 11)."""
        file_object = self._upload(FileObject.Category.DOCUMENT, name='post_attachment.pdf')
        blob_id = file_object.blob_id
        post = Post.objects.create(author=self.user, content='Пост под удаление')
        PostFile.objects.create(post=post, file_object=file_object)

        post.delete()

        self.assertFalse(PostFile.objects.filter(file_object=file_object).exists())
        self.assertBlobOrphaned(blob_id)


class PhonebookPurgeTest(ConsumerPurgeTestMixin, TestCase):
    """У справочника особый случай: ссылка снимается не удалением строки, а
    подменой file_object на живой записи при замене файла. Сигнал
    post_delete здесь не срабатывает — это и есть тот случай, ради которого
    StorageService.detach() остаётся публичным API."""

    def setUp(self):
        self.user = User.objects.create_user(username='phone_admin', password='pass12345')

    def test_deleting_phonebook_entry_orphans_blob(self):
        file_object = self._upload(FileObject.Category.CATALOG, name='book.pdf')
        blob_id = file_object.blob_id
        entry = Phonebook.objects.create(title='Справочник 2026', file_object=file_object)

        entry.delete()

        self.assertBlobOrphaned(blob_id)

    def test_replacing_file_detaches_the_previous_one(self):
        old_object = self._upload(FileObject.Category.CATALOG, name='old.pdf', content=b'old book')
        old_blob_id = old_object.blob_id
        entry = Phonebook.objects.create(title='Справочник', file_object=old_object)

        new_object = self._upload(FileObject.Category.CATALOG, name='new.pdf', content=b'new book')
        entry.file_object = new_object
        entry.save(update_fields=['file_object'])
        StorageService.detach(old_object, user=self.user, consumer='phonebook.Phonebook')

        self.assertBlobOrphaned(old_blob_id)
        self.assertEqual(
            FileBlob.objects.get(pk=new_object.blob_id).status, FileBlob.Status.ACTIVE,
            'Новый файл справочника не должен пострадать от отвязки старого',
        )
