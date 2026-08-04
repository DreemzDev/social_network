"""Подпапки в обменнике (ExchangeFolder).

До этой модели у обменника не было вложенности вообще: список папок был
равен списку сотрудников, без возможности разложить файлы по проектам
внутри своей папки (ARCHITECTURE.md, раздел 1.1 старой редакции).

ExchangeFolder — подпапка внутри личной папки владельца, а не
самостоятельная папка: у неё обязателен owner, но управлять ею (создавать,
удалять) может ЛЮБОЙ сотрудник — так же, как положить файл в чужую папку.
Первая версия ошибочно ограничивала создание/удаление только владельцем
личной папки (по аналогии с deptdocs.DepartmentFolder), что противоречило
открытой природе обменника: пользователи не могли создавать подпапки в
чужих папках и не видели кнопок управления там же. Здесь проверяется
исправленное поведение и то, что удаление подпапки не теряет blob'ы
каскадом — тот же класс утечки, что чинился для CatalogFolder/DepartmentFolder
в test_cascade.py.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from exchange.models import ExchangeFile, ExchangeFolder
from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class ExchangeFolderModelTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='folder_owner', password='x')
        self.creator = User.objects.create_user(username='folder_creator', password='x')
        self.stranger = User.objects.create_user(username='folder_stranger', password='x')

    def test_owner_can_delete_folder(self):
        folder = ExchangeFolder.objects.create(name='Проект Альфа', owner=self.owner, created_by=self.creator)
        self.assertTrue(folder.can_be_deleted_by(self.owner))

    def test_creator_can_delete_folder_even_if_not_owner(self):
        """Кто-то положил подпапку не в свою личную папку, ошибся — должен
        суметь исправить, как и с файлами (can_be_deleted_by)."""
        folder = ExchangeFolder.objects.create(name='Проект Альфа', owner=self.owner, created_by=self.creator)
        self.assertTrue(folder.can_be_deleted_by(self.creator))

    def test_stranger_cannot_delete_folder(self):
        folder = ExchangeFolder.objects.create(name='Проект Альфа', owner=self.owner, created_by=self.creator)
        self.assertFalse(folder.can_be_deleted_by(self.stranger))


class ExchangeFolderViewTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='view_owner', password='pass12345')
        self.stranger = User.objects.create_user(username='view_stranger', password='pass12345')

    def test_owner_can_create_subfolder_in_own_root(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('exchange_folder_create', args=[self.owner.pk]), {'name': 'Проект Альфа'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ExchangeFolder.objects.filter(owner=self.owner, name='Проект Альфа').exists()
        )

    def test_any_employee_can_create_subfolder_in_someone_elses_root(self):
        """Обменник открыт на запись: создать подпапку в чужой личной папке
        можно так же, как положить туда файл (ARCHITECTURE.md, раздел 1.1)."""
        self.client.force_login(self.stranger)
        response = self.client.post(
            reverse('exchange_folder_create', args=[self.owner.pk]), {'name': 'Общий проект'},
        )

        self.assertEqual(response.status_code, 200)
        folder = ExchangeFolder.objects.get(name='Общий проект')
        self.assertEqual(folder.owner_id, self.owner.pk)
        self.assertEqual(folder.created_by_id, self.stranger.pk)

    def test_nested_subfolder_must_belong_to_same_owner(self):
        """parent_id нельзя подставить из чужой личной папки — иначе можно
        было бы прицепить подпапку в дерево другого сотрудника, у которого
        parent и owner не совпадают."""
        other_owner = User.objects.create_user(username='view_other_owner', password='pass12345')
        foreign_parent = ExchangeFolder.objects.create(
            name='Чужой корень', owner=other_owner, created_by=other_owner,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('exchange_folder_create', args=[self.owner.pk]),
            {'name': 'Подпапка', 'parent_id': foreign_parent.pk},
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_can_delete_folder(self):
        folder = ExchangeFolder.objects.create(name='Удаляемая', owner=self.owner, created_by=self.stranger)
        self.client.force_login(self.owner)

        response = self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExchangeFolder.objects.filter(pk=folder.pk).exists())

    def test_creator_can_delete_folder_even_in_someone_elses_root(self):
        folder = ExchangeFolder.objects.create(name='Моя ошибка', owner=self.owner, created_by=self.stranger)
        self.client.force_login(self.stranger)

        response = self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExchangeFolder.objects.filter(pk=folder.pk).exists())

    def test_third_party_cannot_delete_folder(self):
        """Не владелец и не создатель — удалить не может, даже при открытом
        обменнике (тот же принцип, что и для файлов)."""
        third_party = User.objects.create_user(username='view_third_party', password='pass12345')
        folder = ExchangeFolder.objects.create(name='Не трогать', owner=self.owner, created_by=self.stranger)
        self.client.force_login(third_party)

        response = self.client.post(reverse('exchange_folder_delete', args=[folder.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ExchangeFolder.objects.filter(pk=folder.pk).exists())

    def test_folder_content_visible_to_everyone(self):
        """Содержимое подпапки видно всем сотрудникам — как и раньше для
        корня личной папки, вложенность прав не меняет."""
        folder = ExchangeFolder.objects.create(name='Общий проект', owner=self.owner, created_by=self.owner)
        self.client.force_login(self.stranger)

        response = self.client.get(reverse('exchange_subfolder', args=[self.owner.pk, folder.pk]))

        self.assertEqual(response.status_code, 200)

    def test_any_employee_can_upload_into_subfolder(self):
        """Загрузка файла в подпапку чужой личной папки — тот же принцип
        открытости, что и для корня."""
        folder = ExchangeFolder.objects.create(name='Общий проект', owner=self.owner, created_by=self.owner)
        self.client.force_login(self.stranger)
        uploaded = SimpleUploadedFile('report.pdf', b'report content', content_type='application/pdf')

        response = self.client.post(
            reverse('exchange_upload', args=[self.owner.pk]),
            {'files': [uploaded], 'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ExchangeFile.objects.filter(owner=self.owner, folder=folder, uploaded_by=self.stranger).exists()
        )


class ExchangeFolderCascadeTest(TestCase):
    """Тот же класс утечки, что чинился в test_cascade.py для
    CatalogFolder/DepartmentFolder: удаление папки не должно оставлять
    blob'ы ACTIVE без единой ссылки."""

    def setUp(self):
        self.user = User.objects.create_user(username='cascade_folder_owner', password='x')

    def _upload(self, name='folder_cascade.pdf', content=b'folder cascade content'):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=FileObject.Category.EXCHANGE)

    def test_deleting_subfolder_orphans_blobs_of_its_files(self):
        file_object = self._upload()
        blob_id = file_object.blob_id
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=folder, uploaded_by=self.user,
        )

        folder.delete()

        self.assertFalse(ExchangeFile.objects.filter(file_object=file_object).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)

    def test_deleting_parent_subfolder_orphans_blobs_in_nested_subfolders(self):
        file_object = self._upload(name='nested.pdf', content=b'nested content')
        blob_id = file_object.blob_id
        root = ExchangeFolder.objects.create(name='Родитель', owner=self.user, created_by=self.user)
        child = ExchangeFolder.objects.create(
            name='Ребёнок', owner=self.user, parent=root, created_by=self.user,
        )
        ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=child, uploaded_by=self.user,
        )

        root.delete()

        self.assertFalse(ExchangeFolder.objects.filter(pk=child.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)

    def test_deleting_subfolder_does_not_orphan_blob_still_used_elsewhere(self):
        file_object = self._upload(name='shared.pdf', content=b'shared content')
        blob_id = file_object.blob_id
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=folder, uploaded_by=self.user,
        )
        surviving = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=None, uploaded_by=self.user,
        )

        folder.delete()

        self.assertTrue(ExchangeFile.objects.filter(pk=surviving.pk).exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ACTIVE)
