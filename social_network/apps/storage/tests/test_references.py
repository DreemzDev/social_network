"""Регрессионные тесты на обнаружение ссылок (_object_has_references).

Повод: первая реализация обходила FileObject._meta.get_fields() в поисках
обратных связей, но все модели-потребители объявляют FK с related_name='+',
который подавляет создание обратной связи. В результате функция ВСЕГДА
возвращала False, и detach() пытался удалить FileObject даже когда на него
ссылались другие записи. От тихой потери данных спасал только
on_delete=PROTECT, превращавший баг в ProtectedError (500 у пользователя).
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from exchange.models import ExchangeFile
from storage.models import FileBlob, FileObject
from storage.services import StorageService, _iter_consumer_fields, _object_has_references

User = get_user_model()


class ConsumerDiscoveryTest(TestCase):
    """Все модели-потребители должны находиться интроспекцией, несмотря на
    related_name='+'."""

    def test_all_known_consumers_are_discovered(self):
        discovered = {f'{model._meta.label}.{field}' for model, field in _iter_consumer_fields()}

        expected = {
            'exchange.ExchangeFile.file_object',
            'catalog.CatalogDocument.file_object',
            'deptdocs.DepartmentDocument.file_object',
            'posts.PostFile.file_object',
            'phonebook.Phonebook.file_object',
        }

        missing = expected - discovered
        self.assertEqual(
            missing, set(),
            f'Эти потребители storage не найдены интроспекцией: {missing}. '
            f'detach() не увидит их ссылки и преждевременно осиротит файл.',
        )


class ReferenceDetectionTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='ref_owner', password='x')
        self.other = User.objects.create_user(username='ref_other', password='x')

    def _upload(self, name='ref.pdf', content=b'reference test content'):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=FileObject.Category.EXCHANGE)

    def test_has_references_is_true_while_a_consumer_row_exists(self):
        file_object = self._upload()
        ExchangeFile.objects.create(file_object=file_object, owner=self.user, uploaded_by=self.user)

        self.assertTrue(
            _object_has_references(file_object),
            'Пока существует ExchangeFile со ссылкой, файл считается используемым',
        )

    def test_has_references_is_false_when_no_consumer_rows_left(self):
        file_object = self._upload()
        exchange_file = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user
        )
        exchange_file.delete()

        self.assertFalse(_object_has_references(file_object))

    def test_detach_keeps_file_object_while_another_consumer_still_references_it(self):
        """Ключевой сценарий: два потребителя ссылаются на один FileObject,
        один отвязывается — FileObject и blob обязаны выжить."""
        file_object = self._upload()
        first = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user
        )
        second = ExchangeFile.objects.create(
            file_object=file_object, owner=self.other, uploaded_by=self.user
        )
        blob_id = file_object.blob_id

        first.delete()
        StorageService.detach(file_object, user=self.user, consumer='test')

        self.assertTrue(
            FileObject.objects.filter(pk=file_object.pk).exists(),
            'FileObject удалён, хотя на него ещё ссылается второй ExchangeFile',
        )
        self.assertEqual(
            FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ACTIVE,
            'blob осиротел, хотя файл ещё используется',
        )
        self.assertTrue(ExchangeFile.objects.filter(pk=second.pk).exists())

    def test_detach_orphans_blob_only_after_last_reference_is_gone(self):
        file_object = self._upload()
        first = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user
        )
        second = ExchangeFile.objects.create(
            file_object=file_object, owner=self.other, uploaded_by=self.user
        )
        blob_id = file_object.blob_id

        first.delete()
        StorageService.detach(file_object, user=self.user, consumer='test')
        second.delete()
        StorageService.detach(file_object, user=self.user, consumer='test')

        self.assertFalse(FileObject.objects.filter(pk=file_object.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)
