"""StorageService.copy_reference() — копирование/пересылка файла между
модулями без повторной загрузки содержимого.

Отличие от upload(): здесь нет файла для записи на диск, дедупликация не
нужна — файл уже лежит на диске под своим checksum. Копия — вторая
именованная ссылка (FileObject) на тот же blob, ровно то отношение
blob/object, ради которого эти модели разведены (ARCHITECTURE.md, раздел 2).
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from storage.exceptions import QuotaExceededError
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class CopyReferenceTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='copy_user', password='x')
        self.other = User.objects.create_user(username='copy_other', password='x')

    def _upload(self, name='copy_src.pdf', content=b'copy test content', user=None):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=user or self.user, category=FileObject.Category.CATALOG)

    def test_copy_creates_new_file_object_on_same_blob(self):
        original = self._upload()
        copy = StorageService.copy_reference(
            original, user=self.other, category=FileObject.Category.EXCHANGE,
        )

        self.assertNotEqual(copy.pk, original.pk)
        self.assertEqual(copy.blob_id, original.blob_id)
        self.assertEqual(copy.category, FileObject.Category.EXCHANGE)
        self.assertEqual(copy.uploaded_by_id, self.other.pk)

    def test_copy_does_not_write_a_second_file_to_disk(self):
        """Никакой новой физической копии — тот же blob, значит и тот же
        файл на диске. Проверяем через отсутствие изменений в FileBlob."""
        original = self._upload()
        blob_count_before = FileBlob.objects.count()

        StorageService.copy_reference(original, user=self.other, category=FileObject.Category.EXCHANGE)

        self.assertEqual(FileBlob.objects.count(), blob_count_before)

    def test_copy_preserves_original_name_by_default(self):
        original = self._upload(name='report.pdf')
        copy = StorageService.copy_reference(original, user=self.other, category=FileObject.Category.EXCHANGE)

        self.assertEqual(copy.original_name, 'report.pdf')

    def test_copy_can_override_name(self):
        original = self._upload(name='report.pdf')
        copy = StorageService.copy_reference(
            original, user=self.other, category=FileObject.Category.EXCHANGE,
            original_name='Отчёт (переслан).pdf',
        )

        self.assertEqual(copy.original_name, 'Отчёт (переслан).pdf')

    def test_deleting_original_does_not_affect_the_copy(self):
        """Обратная сторона дедупликации: удаление одной ссылки не должно
        трогать другую, пока обе есть — тот же инвариант, что и в
        test_references.py, но для copy_reference."""
        from catalog.models import CatalogDocument, CatalogFolder
        from exchange.models import ExchangeFile

        original = self._upload()
        folder = CatalogFolder.objects.create(name='Тест', created_by=self.user)
        document = CatalogDocument.objects.create(
            folder=folder, file_object=original, title='Оригинал', uploaded_by=self.user,
        )

        copy = StorageService.copy_reference(original, user=self.other, category=FileObject.Category.EXCHANGE)
        exchange_file = ExchangeFile.objects.create(
            file_object=copy, owner=self.other, uploaded_by=self.other,
        )

        document.delete()

        self.assertTrue(FileObject.objects.filter(pk=copy.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=original.blob_id).status, FileBlob.Status.ACTIVE)
        self.assertTrue(ExchangeFile.objects.filter(pk=exchange_file.pk).exists())

    def test_copy_respects_quota_for_new_usage(self):
        """Квота проверяется по blob.size — если получатель ещё не
        "занимал" этот blob в своей квоте, копия считается новым
        использованием и может её превысить.

        override_settings работает потому, что services читает
        STORAGE_USER_QUOTA при вызове, а не при импорте модуля. Пока это
        была константа уровня модуля, тест был вынужден подменять
        services.USER_QUOTA руками и возвращать значение в finally."""
        # Файл грузится без ограничения квоты (её ещё не было при загрузке),
        # ограничение включается только к моменту копирования.
        original = self._upload(content=b'x' * 200)

        with override_settings(STORAGE_USER_QUOTA=100):
            with self.assertRaises(QuotaExceededError):
                StorageService.copy_reference(original, user=self.other, category=FileObject.Category.EXCHANGE)

    @override_settings(STORAGE_USER_QUOTA=100)
    def test_copy_does_not_double_count_quota_for_same_user(self):
        """Пользователь копирует СВОЙ ЖЕ файл в другой раздел — blob уже
        учтён в его квоте, повторно копия не должна её раздувать."""
        original = self._upload(content=b'x' * 90, user=self.user)

        # Копия на того же пользователя — blob уже "его", квота не растёт вдвое.
        copy = StorageService.copy_reference(
            original, user=self.user, category=FileObject.Category.EXCHANGE,
        )
        self.assertIsNotNone(copy.pk)
