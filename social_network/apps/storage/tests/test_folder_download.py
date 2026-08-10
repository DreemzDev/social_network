"""Скачивание папки целиком одним архивом.

Отличие от массового скачивания выбранного (test_bulk_download.py) ровно
одно, но оно и есть вся суть фичи: внутри архива воспроизводится дерево
папок. Плоский архив здесь бессмыслен — папку скачивают как раз затем,
чтобы получить её структуру.

Второе, что проверяется тщательно, — обход поддерева у приватного доступа.
allowed_users задаётся НА ПАПКЕ, поэтому вложенная может быть закрыта при
открытой родительской, и архив не должен становиться способом выгрузить
то, что в интерфейсе даже не показывается.
"""

import io
import shutil
import tempfile
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile, ExchangeFolder
from storage.models import FileObject
from storage.services import StorageService
from storage.utils import folder_subtree

User = get_user_model()


def read_archive(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))


class MediaIsolatedTestCase(TestCase):
    """Свой MEDIA_ROOT на каждый тест — содержимое читается с диска."""

    prefix = 'folder-zip-test-'

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix=self.prefix)
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def upload(self, user, name, content, category=FileObject.Category.CATALOG):
        return StorageService.upload(
            SimpleUploadedFile(name, content), user=user, category=category,
        )


class FolderSubtreeTest(TestCase):
    """Обход дерева — общий для трёх модулей, раньше был тремя копиями."""

    def setUp(self):
        self.user = User.objects.create_user(username='subtree_user', password='pass12345')

    def test_paths_start_with_the_folder_itself(self):
        """Папка приезжает в архиве вместе со своим именем, а не
        вываливается содержимым в корень."""
        root = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        year = CatalogFolder.objects.create(name='2024', parent=root, created_by=self.user)
        month = CatalogFolder.objects.create(name='Май', parent=year, created_by=self.user)

        paths = folder_subtree(root)

        self.assertEqual(paths[root.pk], ('Приказы',))
        self.assertEqual(paths[year.pk], ('Приказы', '2024'))
        self.assertEqual(paths[month.pk], ('Приказы', '2024', 'Май'))

    def test_sibling_branches_are_not_mixed(self):
        root = CatalogFolder.objects.create(name='Корень', created_by=self.user)
        first = CatalogFolder.objects.create(name='А', parent=root, created_by=self.user)
        second = CatalogFolder.objects.create(name='Б', parent=root, created_by=self.user)
        deep = CatalogFolder.objects.create(name='глубоко', parent=second, created_by=self.user)

        paths = folder_subtree(root)

        self.assertEqual(paths[first.pk], ('Корень', 'А'))
        self.assertEqual(paths[deep.pk], ('Корень', 'Б', 'глубоко'))

    def test_queryset_cuts_the_whole_branch(self):
        """Для приватного доступа важно именно это: закрытая папка обрывает
        ветку целиком, а не пропускается с сохранением потомков."""
        root = CatalogFolder.objects.create(name='Корень', created_by=self.user)
        closed = CatalogFolder.objects.create(name='Закрытая', parent=root, created_by=self.user)
        under_closed = CatalogFolder.objects.create(name='Внутри', parent=closed, created_by=self.user)

        paths = folder_subtree(root, queryset=CatalogFolder.objects.exclude(pk=closed.pk))

        self.assertNotIn(closed.pk, paths)
        self.assertNotIn(under_closed.pk, paths, 'потомок закрытой папки не должен попасть в обход')


class CatalogFolderDownloadTest(MediaIsolatedTestCase):

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='folder_zip_user', password='pass12345')
        self.client.force_login(self.user)

        self.root = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        self.year = CatalogFolder.objects.create(name='2024', parent=self.root, created_by=self.user)

        self.add('в корне папки.pdf', b'ROOT LEVEL', self.root)
        self.add('приказ.pdf', b'ORDER 2024', self.year)

    def add(self, name, content, folder):
        return CatalogDocument.objects.create(
            folder=folder, file_object=self.upload(self.user, name, content),
            title=name, uploaded_by=self.user,
        )

    def download(self, folder, **params):
        return self.client.get(reverse('catalog_folder_download', args=[folder.pk]), params)

    def test_archive_reproduces_the_folder_tree(self):
        archive = read_archive(self.download(self.root))

        self.assertEqual(
            sorted(archive.namelist()),
            ['Приказы/2024/приказ.pdf', 'Приказы/в корне папки.pdf'],
        )
        self.assertEqual(archive.read('Приказы/2024/приказ.pdf'), b'ORDER 2024')

    def test_downloading_a_subfolder_takes_only_it(self):
        archive = read_archive(self.download(self.year))

        self.assertEqual(archive.namelist(), ['2024/приказ.pdf'])

    def test_archive_is_named_after_the_folder(self):
        response = self.download(self.root)

        self.assertIn("filename*=utf-8''", response['Content-Disposition'])
        self.assertIn('%D0%9F%D1%80%D0%B8%D0%BA%D0%B0%D0%B7%D1%8B.zip', response['Content-Disposition'])

    def test_documents_outside_the_folder_are_not_included(self):
        other = CatalogFolder.objects.create(name='Другая', created_by=self.user)
        self.add('чужой.pdf', b'OTHER', other)
        self.add('в корне каталога.pdf', b'CATALOG ROOT', None)

        archive = read_archive(self.download(self.root))

        self.assertNotIn('чужой.pdf', ' '.join(archive.namelist()))
        self.assertNotIn('в корне каталога.pdf', ' '.join(archive.namelist()))

    def test_trashed_documents_are_skipped(self):
        trashed = self.add('удалённый.pdf', b'TRASHED', self.root)
        trashed.is_deleted = True
        trashed.save(update_fields=['is_deleted'])

        archive = read_archive(self.download(self.root))

        self.assertNotIn('удалённый.pdf', ' '.join(archive.namelist()))

    def test_same_name_in_different_folders_is_not_renamed(self):
        """Разводить по имени нужно только внутри одной папки: два
        «приказ.pdf» в разных каталогах — норма, и приписка «(2)» к одному
        из них была бы враньём о его имени."""
        self.add('приказ.pdf', b'ROOT ORDER', self.root)

        names = read_archive(self.download(self.root)).namelist()

        self.assertIn('Приказы/приказ.pdf', names)
        self.assertIn('Приказы/2024/приказ.pdf', names)

    def test_same_name_inside_one_folder_is_disambiguated(self):
        """А вот внутри одной папки одинаковые имена — обычное дело из-за
        дедупликации, и без разведения пользователь получил бы один файл
        вместо двух."""
        self.add('приказ.pdf', b'FIRST', self.year)

        names = read_archive(self.download(self.year)).namelist()

        self.assertEqual(len(names), 2)
        self.assertIn('2024/приказ.pdf', names)
        self.assertIn('2024/приказ (2).pdf', names)

    def test_empty_folder_is_refused_with_a_reason(self):
        """Не пустой архив: он выглядел бы как «скачалось, но всё пропало»."""
        empty = CatalogFolder.objects.create(name='Пустая', created_by=self.user)

        response = self.download(empty)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Нечего скачивать', response.json()['error'])

    def test_check_mode_reports_the_summary(self):
        response = self.download(self.root, check='1')

        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['skipped'], 0)

    @override_settings(STORAGE_ZIP_MAX_FILES=1)
    def test_limits_apply_to_folders_too(self):
        """Поддерево может оказаться сколь угодно большим, а архив собирает
        воркер Daphne — пределы тут нужнее, чем при ручном выделении."""
        response = self.download(self.root)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Слишком много файлов', response.json()['error'])

    def test_anonymous_is_sent_to_login(self):
        response = Client().get(reverse('catalog_folder_download', args=[self.root.pk]))

        self.assertEqual(response.status_code, 302)


class DeptdocsFolderDownloadTest(MediaIsolatedTestCase):
    """Права на каждую папку поддерева, а не только на корневую."""

    prefix = 'folder-zip-dept-'

    def setUp(self):
        super().setUp()
        self.member = User.objects.create_user(username='folder_zip_member', password='pass12345')
        self.outsider = User.objects.create_user(username='folder_zip_outsider', password='pass12345')

        self.root = DepartmentFolder.objects.create(name='Отдел', created_by=self.member)
        self.root.allowed_users.set([self.member])

        self.open_child = DepartmentFolder.objects.create(
            name='Открытая', parent=self.root, created_by=self.member,
        )
        self.open_child.allowed_users.set([self.member])

        self.closed_child = DepartmentFolder.objects.create(
            name='Закрытая', parent=self.root, created_by=self.outsider,
        )
        self.closed_child.allowed_users.set([self.outsider])

        self.add('свой.pdf', b'MINE', self.root)
        self.add('вложенный.pdf', b'NESTED', self.open_child)
        self.add('секрет.pdf', b'SECRET', self.closed_child)

    def add(self, name, content, folder):
        return DepartmentDocument.objects.create(
            folder=folder,
            file_object=self.upload(self.member, name, content, FileObject.Category.DOCUMENT),
            title=name, uploaded_by=self.member,
        )

    def test_closed_subfolder_is_not_in_the_archive(self):
        self.client.force_login(self.member)

        archive = read_archive(
            self.client.get(reverse('deptdocs_folder_download', args=[self.root.pk]))
        )

        self.assertEqual(
            sorted(archive.namelist()), ['Отдел/Открытая/вложенный.pdf', 'Отдел/свой.pdf'],
        )
        self.assertNotIn('секрет.pdf', ' '.join(archive.namelist()))

    def test_outsider_cannot_download_the_folder_at_all(self):
        client = Client()
        client.force_login(self.outsider)

        response = client.get(reverse('deptdocs_folder_download', args=[self.root.pk]))

        self.assertEqual(response.status_code, 403)

    def test_member_of_the_closed_folder_gets_its_content(self):
        """Обратная проверка: запрет не должен ломать законный доступ."""
        client = Client()
        client.force_login(self.outsider)

        archive = read_archive(
            client.get(reverse('deptdocs_folder_download', args=[self.closed_child.pk]))
        )

        self.assertEqual(archive.read('Закрытая/секрет.pdf'), b'SECRET')


class ExchangeFolderDownloadTest(MediaIsolatedTestCase):

    prefix = 'folder-zip-exchange-'

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username='folder_zip_owner', password='pass12345',
            first_name='Иван', last_name='Иванов',
        )
        self.colleague = User.objects.create_user(username='folder_zip_mate', password='pass12345')

        self.project = ExchangeFolder.objects.create(
            name='Проект', owner=self.owner, created_by=self.owner,
        )
        self.nested = ExchangeFolder.objects.create(
            name='Смета', owner=self.owner, parent=self.project, created_by=self.owner,
        )

        self.add('в корне личной.pdf', b'PERSONAL ROOT', None)
        self.add('в проекте.pdf', b'IN PROJECT', self.project)
        self.add('в смете.pdf', b'IN NESTED', self.nested)

    def add(self, name, content, folder):
        return ExchangeFile.objects.create(
            file_object=self.upload(self.owner, name, content, FileObject.Category.EXCHANGE),
            owner=self.owner, folder=folder, uploaded_by=self.owner,
        )

    def test_subfolder_download_includes_nested(self):
        self.client.force_login(self.colleague)

        archive = read_archive(
            self.client.get(reverse('exchange_folder_download', args=[self.project.pk]))
        )

        self.assertEqual(
            sorted(archive.namelist()), ['Проект/Смета/в смете.pdf', 'Проект/в проекте.pdf'],
        )

    def test_personal_folder_download_includes_root_level_files(self):
        """Папка верхнего уровня обменника — сам сотрудник, а не запись
        ExchangeFolder: у файлов в её корне folder=None, и обычным обходом
        дерева они недостижимы в принципе."""
        self.client.force_login(self.colleague)

        archive = read_archive(
            self.client.get(reverse('exchange_personal_download', args=[self.owner.pk]))
        )

        self.assertEqual(
            sorted(archive.namelist()),
            ['Проект/Смета/в смете.pdf', 'Проект/в проекте.pdf', 'в корне личной.pdf'],
        )

    def test_personal_archive_is_named_after_the_employee(self):
        self.client.force_login(self.colleague)

        response = self.client.get(reverse('exchange_personal_download', args=[self.owner.pk]))

        self.assertIn('%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2', response['Content-Disposition'])

    def test_other_employees_files_are_not_included(self):
        other = User.objects.create_user(username='folder_zip_other', password='pass12345')
        ExchangeFile.objects.create(
            file_object=self.upload(other, 'чужой.pdf', b'OTHER', FileObject.Category.EXCHANGE),
            owner=other, uploaded_by=other,
        )
        self.client.force_login(self.colleague)

        archive = read_archive(
            self.client.get(reverse('exchange_personal_download', args=[self.owner.pk]))
        )

        self.assertNotIn('чужой.pdf', ' '.join(archive.namelist()))
