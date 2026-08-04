"""Единая страница корзины (/storage/trash/) с табами.

Повод: у каждого из трёх модулей файлового менеджера была своя корзина по
своему адресу с одинаковым внешним видом (общий layout fm-scope, тот же
заголовок «Корзина» в хлебной крошке) — легко открыть корзину не того
модуля и решить, что удалённый файл пропал. Эта вьюха собирает содержимое
всех трёх корзин на одной странице, с той же логикой видимости, что и
раньше в отдельных TrashView каждого модуля.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument, CatalogFolder
from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class UnifiedTrashViewTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='unified_trash_user', password='pass12345')
        self.client.force_login(self.user)

    def _upload(self, category, name='trash_page.pdf', content=None):
        content = content or name.encode()
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        return StorageService.upload(uploaded, user=self.user, category=category)

    @staticmethod
    def _tab_items(response, key):
        """Содержимое одного таба.

        Контекст вьюхи — список табов, а не три отдельные переменные:
        описание таба (подпись, счётчик, шаблон карточки, признак
        усечения) нужно шаблону целиком, и держать это пятью
        параллельными списками значило бы следить за их синхронностью
        руками."""
        tab = next(t for t in response.context['tabs'] if t['key'] == key)
        return tab['items']

    def test_page_shows_own_exchange_trash(self):
        file_object = self._upload(FileObject.Category.EXCHANGE, name='exch.pdf')
        ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user, is_deleted=True,
        )

        response = self.client.get(reverse('storage_trash'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(file_object, [f.file_object for f in self._tab_items(response, 'exchange')])

    def test_page_shows_all_catalog_trash(self):
        """Каталог общедоступен — корзина показывает все документы, а не
        только свои, как и было в отдельном CatalogTrashView."""
        other_user = User.objects.create_user(username='unified_trash_other', password='x')
        file_object = self._upload(FileObject.Category.CATALOG, name='cat.pdf')
        folder = CatalogFolder.objects.create(name='Приказы', created_by=other_user)
        document = CatalogDocument.objects.create(
            folder=folder, file_object=file_object, title='Чужой документ',
            uploaded_by=other_user, is_deleted=True,
        )

        response = self.client.get(reverse('storage_trash'))

        self.assertIn(document, self._tab_items(response, 'catalog'))

    def test_page_shows_only_accessible_deptdocs_trash(self):
        """Приватный доступ — корзина показывает только документы из папок,
        доступных текущему пользователю, как и раньше."""
        accessible_folder = DepartmentFolder.objects.create(name='Мой отдел', created_by=self.user)
        accessible_folder.allowed_users.add(self.user)
        foreign_folder = DepartmentFolder.objects.create(name='Чужой отдел', created_by=self.user)

        visible_file = self._upload(FileObject.Category.DOCUMENT, name='visible.pdf')
        hidden_file = self._upload(FileObject.Category.DOCUMENT, name='hidden.pdf', content=b'hidden')

        visible_doc = DepartmentDocument.objects.create(
            folder=accessible_folder, file_object=visible_file, title='Видимый',
            uploaded_by=self.user, is_deleted=True,
        )
        hidden_doc = DepartmentDocument.objects.create(
            folder=foreign_folder, file_object=hidden_file, title='Скрытый',
            uploaded_by=self.user, is_deleted=True,
        )

        response = self.client.get(reverse('storage_trash'))

        deptdocs_items = self._tab_items(response, 'deptdocs')
        self.assertIn(visible_doc, deptdocs_items)
        self.assertNotIn(hidden_doc, deptdocs_items)

    def test_tab_query_param_is_passed_through(self):
        response = self.client.get(reverse('storage_trash'), {'tab': 'catalog'})
        self.assertEqual(response.context['active_tab'], 'catalog')

    def test_default_tab_is_exchange(self):
        response = self.client.get(reverse('storage_trash'))
        self.assertEqual(response.context['active_tab'], 'exchange')
