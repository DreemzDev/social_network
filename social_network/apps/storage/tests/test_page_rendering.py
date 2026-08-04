"""Смоук-рендер всех страниц файлового менеджера.

Зачем отдельный файл: до него backend был покрыт тестами, а шаблоны — нет,
и проверялись они вручную через curl под реальным логином. Ошибки уровня
шаблона (несуществующее имя маршрута в {% url %}, фильтр без {% load %},
переменная, которую вьюха перестала класть в контекст) при этом не ловились
ничем: юнит-тесты вьюх обычно смотрят на JSON-ответы и контекст, а не на
факт успешного рендера страницы целиком.

Проверяется именно рендер (200 и отсутствие исключений), а не вёрстка:
сравнивать HTML с эталоном бессмысленно — он меняется при каждой правке
дизайна, и такой тест ломался бы чаще, чем находил ошибки.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile, ExchangeFolder
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class FileManagerPageRenderingTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='render_user', password='pass12345', first_name='Иван', last_name='Иванов',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _upload(self, category, name):
        uploaded = SimpleUploadedFile(name, name.encode() * 4, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=category)

    def _exchange_file(self, folder=None, name='exch_render.pdf', is_deleted=False):
        return ExchangeFile.objects.create(
            file_object=self._upload(FileObject.Category.EXCHANGE, name),
            owner=self.user, uploaded_by=self.user, folder=folder, is_deleted=is_deleted,
        )

    def _catalog_document(self, folder=None, name='cat_render.pdf', is_deleted=False):
        return CatalogDocument.objects.create(
            file_object=self._upload(FileObject.Category.CATALOG, name),
            folder=folder, title='Документ каталога', uploaded_by=self.user, is_deleted=is_deleted,
        )

    def _dept_folder(self, name='Отдел'):
        folder = DepartmentFolder.objects.create(name=name, created_by=self.user)
        folder.allowed_users.add(self.user)
        return folder

    def _dept_document(self, folder, name='dept_render.pdf', is_deleted=False):
        return DepartmentDocument.objects.create(
            file_object=self._upload(FileObject.Category.DOCUMENT, name),
            folder=folder, title='Документ отдела', uploaded_by=self.user, is_deleted=is_deleted,
        )

    # --- обменник ---

    def test_exchange_folder_list_renders(self):
        response = self.client.get(reverse('exchange_inbox'))
        self.assertEqual(response.status_code, 200)

    def test_exchange_folder_renders_with_content(self):
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        self._exchange_file()
        self._exchange_file(folder=folder, name='in_sub.pdf')

        response = self.client.get(reverse('exchange_folder', args=[self.user.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проект')

    def test_exchange_subfolder_renders(self):
        folder = ExchangeFolder.objects.create(name='Проект', owner=self.user, created_by=self.user)
        self._exchange_file(folder=folder, name='sub_render.pdf')

        response = self.client.get(reverse('exchange_subfolder', args=[self.user.pk, folder.pk]))
        self.assertEqual(response.status_code, 200)

    def test_exchange_trash_renders(self):
        self._exchange_file(name='trashed.pdf', is_deleted=True)
        response = self.client.get(reverse('exchange_trash'))
        self.assertEqual(response.status_code, 200)

    # --- каталог ---

    def test_catalog_root_renders(self):
        self._catalog_document()
        response = self.client.get(reverse('catalog_root'))
        self.assertEqual(response.status_code, 200)

    def test_catalog_folder_renders(self):
        folder = CatalogFolder.objects.create(name='Приказы', created_by=self.user)
        self._catalog_document(folder=folder)

        response = self.client.get(reverse('catalog_folder', args=[folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Приказы')

    def test_catalog_trash_renders(self):
        self._catalog_document(name='cat_trash.pdf', is_deleted=True)
        response = self.client.get(reverse('catalog_trash'))
        self.assertEqual(response.status_code, 200)

    # --- приватный доступ ---

    def test_deptdocs_root_renders(self):
        self._dept_folder()
        response = self.client.get(reverse('deptdocs_list'))
        self.assertEqual(response.status_code, 200)

    def test_deptdocs_folder_renders(self):
        folder = self._dept_folder()
        self._dept_document(folder)

        response = self.client.get(reverse('deptdocs_folder', args=[folder.pk]))
        self.assertEqual(response.status_code, 200)

    def test_deptdocs_trash_renders(self):
        folder = self._dept_folder()
        self._dept_document(folder, name='dept_trash.pdf', is_deleted=True)

        response = self.client.get(reverse('deptdocs_trash'))
        self.assertEqual(response.status_code, 200)

    # --- storage ---

    def test_storage_dashboard_renders(self):
        response = self.client.get(reverse('storage_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_storage_dashboard_renders_for_staff(self):
        """У персонала на дашборде появляется блок сводки по всему
        хранилищу — отдельная ветка шаблона и отдельный запрос
        (get_storage_stats), обычным пользователем не проверяемые."""
        staff = User.objects.create_user(username='render_staff', password='x', is_staff=True)
        self.client.force_login(staff)

        response = self.client.get(reverse('storage_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('storage_stats', response.context)

    def test_unified_trash_renders(self):
        self._exchange_file(name='u_trash.pdf', is_deleted=True)
        response = self.client.get(reverse('storage_trash'))
        self.assertEqual(response.status_code, 200)


class PartialGridRenderingTest(TestCase):
    """?partial=1 отдаёт только сетку карточек — на этом построено живое
    обновление без перезагрузки страницы (storage.utils.PartialGridMixin,
    fm-actions.js: FM.refresh)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='partial_user', password='pass12345')

    def setUp(self):
        self.client.force_login(self.user)

    def _exchange_file(self):
        uploaded = SimpleUploadedFile('partial.pdf', b'partial content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.EXCHANGE)
        return ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user,
        )

    def test_partial_returns_only_grid(self):
        self._exchange_file()

        full = self.client.get(reverse('exchange_folder', args=[self.user.pk]))
        partial = self.client.get(reverse('exchange_folder', args=[self.user.pk]), {'partial': '1'})

        self.assertEqual(partial.status_code, 200)
        # Признак полной страницы — левое меню модулей; в куске сетки его быть
        # не должно, иначе при подстановке в DOM меню задвоится.
        self.assertContains(full, 'Информационный каталог')
        self.assertNotContains(partial, 'Информационный каталог')
        self.assertContains(partial, 'partial.pdf')

    def test_partial_uses_grid_template(self):
        self._exchange_file()
        response = self.client.get(reverse('exchange_folder', args=[self.user.pk]), {'partial': '1'})
        self.assertTemplateUsed(response, 'exchange/_grid.html')
        self.assertTemplateNotUsed(response, 'exchange/folder.html')

    def test_catalog_partial_renders(self):
        response = self.client.get(reverse('catalog_root'), {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'catalog/_grid.html')

    def test_deptdocs_partial_renders(self):
        folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        folder.allowed_users.add(self.user)

        response = self.client.get(reverse('deptdocs_folder', args=[folder.pk]), {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'deptdocs/_grid.html')
