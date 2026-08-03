"""Регрессионные тесты на каскадное удаление записей-потребителей.

Повод: чек-лист ARCHITECTURE.md (раздел 10, пункт 5) запрещает удалять записи
потребителей в обход detach(), но закрывает эту дыру только дисциплиной — «не
пишите queryset.delete()». Каскад от ForeignKey дисциплиной не закрывается:
его никто не пишет явно, он срабатывает сам.

Реальные каскадные пути в проекте:

- CatalogFolder → CatalogDocument (on_delete=CASCADE);
- DepartmentFolder → DepartmentDocument (on_delete=CASCADE);
- CatalogFolder/DepartmentFolder.parent → self (удаление дерева папок);
- User → ExchangeFile (owner, on_delete=CASCADE).

По любому из них запись потребителя исчезает, detach() не вызывается, и blob
остаётся ACTIVE навсегда: механизм ORPHAN его не увидит (ссылок нет, но и
статус не сменился), purge_expired_orphans() до него не доберётся, а
find_untracked_files() не считает его потерянным — запись FileBlob на месте.
Файл занимает диск до ручного вмешательства.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile
from storage.models import FileBlob, FileObject

User = get_user_model()


class CascadeDeleteTest(TestCase):
    """Каскадное удаление владельца записи-потребителя обязано приводить blob
    в ORPHAN так же, как явный detach()."""

    def setUp(self):
        self.user = User.objects.create_user(username='cascade_owner', password='x')

    def _upload(self, name='cascade.pdf', content=b'cascade test content', category=None):
        from storage.services import StorageService

        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(
            uploaded, user=self.user, category=category or FileObject.Category.CATALOG,
        )

    def assertBlobOrphaned(self, blob_id, message):
        blob = FileBlob.objects.get(pk=blob_id)
        self.assertEqual(blob.status, FileBlob.Status.ORPHAN, message)

    def test_deleting_catalog_folder_orphans_blobs_of_cascaded_documents(self):
        file_object = self._upload()
        blob_id = file_object.blob_id
        folder = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        CatalogDocument.objects.create(
            folder=folder, file_object=file_object, title='Приказ №1', uploaded_by=self.user,
        )

        folder.delete()

        self.assertFalse(
            CatalogDocument.objects.filter(file_object=file_object).exists(),
            'Документ должен быть удалён каскадом вместе с папкой',
        )
        self.assertBlobOrphaned(
            blob_id,
            'Папка каталога удалена каскадом, документ вместе с ней, но blob '
            'остался ACTIVE — файл навсегда занимает диск.',
        )

    def test_deleting_department_folder_orphans_blobs_of_cascaded_documents(self):
        file_object = self._upload(category=FileObject.Category.DOCUMENT)
        blob_id = file_object.blob_id
        folder = DepartmentFolder.objects.create(name='Отдел кадров', created_by=self.user)
        folder.allowed_users.add(self.user)
        DepartmentDocument.objects.create(
            folder=folder, file_object=file_object, title='Штатное расписание',
            uploaded_by=self.user,
        )

        folder.delete()

        self.assertBlobOrphaned(
            blob_id,
            'Папка документов отдела удалена каскадом, документ вместе с ней, '
            'но blob остался ACTIVE.',
        )

    def test_deleting_parent_folder_orphans_blobs_from_nested_subfolders(self):
        """Дерево папок: удаление корня каскадит через parent на подпапки, а
        уже оттуда — на документы. Двухуровневый каскад."""
        file_object = self._upload()
        blob_id = file_object.blob_id
        root = CatalogFolder.objects.create(name='Кадры', created_by=self.user)
        child = CatalogFolder.objects.create(name='Приказы 2026', parent=root, created_by=self.user)
        CatalogDocument.objects.create(
            folder=child, file_object=file_object, title='Приказ', uploaded_by=self.user,
        )

        root.delete()

        self.assertFalse(CatalogFolder.objects.filter(pk=child.pk).exists())
        self.assertBlobOrphaned(
            blob_id, 'Каскад через дерево папок оставил blob в ACTIVE.',
        )

    def test_deleting_user_orphans_blobs_of_their_exchange_files(self):
        """ExchangeFile.owner = CASCADE: удаление уволенного сотрудника
        сносит его папку обменника целиком."""
        file_object = self._upload(category=FileObject.Category.EXCHANGE)
        blob_id = file_object.blob_id
        folder_owner = User.objects.create_user(username='cascade_leaver', password='x')
        ExchangeFile.objects.create(
            file_object=file_object, owner=folder_owner, uploaded_by=self.user,
        )

        folder_owner.delete()

        self.assertFalse(ExchangeFile.objects.filter(file_object=file_object).exists())
        self.assertBlobOrphaned(
            blob_id,
            'Пользователь удалён, его файлы обменника ушли каскадом, но blob '
            'остался ACTIVE.',
        )

    def test_queryset_delete_still_orphans_blobs(self):
        """Массовое удаление тоже обязано освобождать blob'ы.

        Django умеет «быстрое удаление» одним DELETE без рассылки сигналов,
        но применяет его только к моделям, у которых нет получателей
        pre_delete/post_delete. Регистрация сигнала storage делает
        can_fast_delete() ложным, и удаление идёт обычным путём.
        Проверяется здесь, потому что чек-лист (раздел 10, пункт 5) прямо
        опирается на это поведение.
        """
        first = self._upload(name='bulk_one.pdf', content=b'bulk content one')
        second = self._upload(name='bulk_two.pdf', content=b'bulk content two')
        folder = CatalogFolder.objects.create(name='Массовое', created_by=self.user)
        for file_object in (first, second):
            CatalogDocument.objects.create(
                folder=folder, file_object=file_object, title='Документ',
                uploaded_by=self.user,
            )

        CatalogDocument.objects.filter(folder=folder).delete()

        self.assertBlobOrphaned(
            first.blob_id, 'queryset.delete() не освободил blob — сработало быстрое удаление',
        )
        self.assertBlobOrphaned(second.blob_id, 'queryset.delete() не освободил второй blob')

    def test_cascade_does_not_orphan_blob_still_used_elsewhere(self):
        """Обратная сторона: каскад не должен осиротить blob, на который
        ссылается ещё один потребитель (дедуплицированный файл)."""
        file_object = self._upload()
        blob_id = file_object.blob_id
        folder = CatalogFolder.objects.create(name='Каталог', created_by=self.user)
        CatalogDocument.objects.create(
            folder=folder, file_object=file_object, title='Документ', uploaded_by=self.user,
        )
        surviving = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user,
        )

        folder.delete()

        self.assertTrue(ExchangeFile.objects.filter(pk=surviving.pk).exists())
        self.assertTrue(
            FileObject.objects.filter(pk=file_object.pk).exists(),
            'FileObject удалён, хотя на него ещё ссылается файл обменника',
        )
        self.assertEqual(
            FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ACTIVE,
            'blob осиротел, хотя файл ещё используется обменником',
        )
