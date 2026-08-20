"""Общий поиск по порталу.

Главные тесты здесь — про права. Поиск ходит в модули напрямую, минуя их
вьюхи, поэтому проверка доступа обязана быть в каждом поставщике: иначе
выдача показывала бы названия чужих документов, даже если сам файл не
скачать. Утечка через заголовки — тоже утечка.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import CatalogDocument
from deptdocs.models import DepartmentDocument, DepartmentFolder
from netmap.models import NetworkAddress, Subnet
from phonebook.models import Phonebook
from profiles.search import search_everything
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class SearchTestCase(TestCase):

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(
            username='searcher', password='pass12345', first_name='Пётр', last_name='Соколов',
        )
        self.stranger = User.objects.create_user(username='stranger', password='pass12345')

    def upload(self, name):
        return StorageService.upload(
            SimpleUploadedFile(name, b'data'), user=self.user, category=FileObject.Category.DOCUMENT,
        )

    def titles(self, query, user, group=None):
        groups = search_everything(query, user)
        return [
            hit['title'] for entry in groups
            if group is None or entry['title'] == group
            for hit in entry['hits']
        ]


class PermissionTest(SearchTestCase):
    """Поиск обязан показывать только то, что человеку и так доступно."""

    def test_department_document_hidden_from_outsider(self):
        """Документы отдела закрыты списком допущенных. Если бы поиск это
        не учитывал, любой сотрудник читал бы названия чужих документов —
        притом что открыть их он не может."""
        folder = DepartmentFolder.objects.create(name='Кадровые приказы', created_by=self.user)
        folder.allowed_users.add(self.user)
        DepartmentDocument.objects.create(
            folder=folder, file_object=self.upload('prikaz.pdf'),
            title='Приказ о премировании', uploaded_by=self.user,
        )

        self.assertIn('Приказ о премировании', self.titles('премировании', self.user))
        self.assertEqual(self.titles('премировании', self.stranger), [])

    def test_document_without_folder_is_invisible_to_everyone(self):
        """Документ вне папки не наследует ничьих прав (см. докстринг
        DepartmentDocument) — в выдаче его быть не должно ни у кого."""
        DepartmentDocument.objects.create(
            folder=None, file_object=self.upload('ничей.pdf'),
            title='Документ без папки', uploaded_by=self.user,
        )

        self.assertEqual(self.titles('без папки', self.user), [])

    def test_netmap_is_staff_only(self):
        """Карта сети закрыта от обычных сотрудников — поиск не должен быть
        обходным путём к ней."""
        subnet = Subnet.objects.create(cidr='10.10.0.0/24')
        NetworkAddress.objects.create(subnet=subnet, ip='10.10.0.5', name='ПК бухгалтерии')

        self.assertEqual(self.titles('10.10.0.5', self.user), [])

        self.user.is_staff = True
        self.user.save()
        self.assertIn('10.10.0.5', self.titles('10.10.0.5', self.user))

    def test_trashed_file_is_not_found(self):
        """Удалённое в корзину не ищется: иначе поиск возвращал бы то, что
        пользователь считает удалённым."""
        document = CatalogDocument.objects.create(
            file_object=self.upload('смета.pdf'), title='Смета на ремонт', uploaded_by=self.user,
        )
        self.assertIn('Смета на ремонт', self.titles('смета', self.user))

        document.is_deleted = True
        document.save()
        self.assertEqual(self.titles('смета', self.user), [])

    def test_deleted_reference_book_is_not_found(self):
        book = Phonebook.objects.create(title='Справочник по режиму')
        self.assertIn('Справочник по режиму', self.titles('режиму', self.user))

        book.is_deleted = True
        book.save()
        self.assertEqual(self.titles('режиму', self.user), [])


class ResultsTest(SearchTestCase):

    def test_finds_employee_by_surname_and_room(self):
        self.user.cab = '301'
        self.user.save()

        self.assertIn('Соколов Пётр', self.titles('Соколов', self.user))
        self.assertIn('Соколов Пётр', self.titles('301', self.user))

    def test_files_from_three_modules_share_one_group(self):
        """Человек ищет файл, а не модуль, в котором тот лежит."""
        CatalogDocument.objects.create(
            file_object=self.upload('инструкция-каталог.pdf'), title='Инструкция каталога',
            uploaded_by=self.user,
        )
        folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        folder.allowed_users.add(self.user)
        DepartmentDocument.objects.create(
            folder=folder, file_object=self.upload('инструкция-отдел.pdf'),
            title='Инструкция отдела', uploaded_by=self.user,
        )

        groups = {entry['title'] for entry in search_everything('инструкция', self.user)}

        self.assertEqual(groups, {'Файлы'})
        self.assertEqual(len(self.titles('инструкция', self.user, group='Файлы')), 2)

    def test_short_query_returns_nothing(self):
        """Один символ совпал бы почти со всем и стоил бы шести запросов в БД."""
        self.assertEqual(search_everything('о', self.user), [])
        self.assertEqual(search_everything('', self.user), [])

    def test_empty_groups_are_not_returned(self):
        """Пустая группа в выпадающем списке — это заголовок без строк."""
        for group in search_everything('Соколов', self.user):
            self.assertTrue(group['hits'])


class SearchViewTest(SearchTestCase):

    def test_anonymous_is_redirected(self):
        response = self.client.get(reverse('global_search'), {'q': 'что-нибудь'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_xhr_returns_only_the_fragment(self):
        """Выпадающему списку нужна разметка результатов, а не целая
        страница: иначе в него подставился бы весь портал вместе с меню."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('global_search'), {'q': 'Соколов'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/layout/search_results.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'Соколов')

    def test_plain_request_returns_the_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('global_search'), {'q': 'Соколов'})

        self.assertTemplateUsed(response, 'profiles/search.html')
        self.assertContains(response, 'Соколов')

    def test_short_query_says_so_instead_of_staying_silent(self):
        """Молчаливый отказ — дефект: человек должен понимать, почему
        ничего не показано."""
        self.client.force_login(self.user)

        response = self.client.get(reverse('global_search'), {'q': 'о'})

        self.assertContains(response, 'хотя бы два символа')
