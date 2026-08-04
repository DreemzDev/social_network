"""Сортировка списков в файловом менеджере (?sort=name|-name|uploaded_at|
-uploaded_at|size|-size) — до этого списки были жёстко упорядочены по
дате загрузки (Meta.ordering модели), без возможности пользователю
выбрать порядок.

Сортировка по size идёт через file_object__blob__size (storage/utils.py):
FileObject.size — python-property, а не поле модели, ORDER BY по нему
на уровне SQL невозможен."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()


class CatalogSortTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='sort_user', password='pass12345')
        self.client.force_login(self.user)

        # Имена и размеры специально не совпадают по порядку с датой
        # загрузки — иначе сортировки было бы не отличить друг от друга.
        self._create_document('B_document.pdf', b'x' * 300)
        self._create_document('A_document.pdf', b'x' * 100)
        self._create_document('C_document.pdf', b'x' * 200)

    def _create_document(self, name, content):
        uploaded = SimpleUploadedFile(name, content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        return CatalogDocument.objects.create(file_object=file_object, title=name, uploaded_by=self.user)

    def test_sort_by_name_ascending(self):
        response = self.client.get(reverse('catalog_root'), {'sort': 'name'})
        titles = [doc.title for doc in response.context['documents']]
        self.assertEqual(titles, sorted(titles))

    def test_sort_by_name_descending(self):
        response = self.client.get(reverse('catalog_root'), {'sort': '-name'})
        titles = [doc.title for doc in response.context['documents']]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_sort_by_size_ascending(self):
        response = self.client.get(reverse('catalog_root'), {'sort': 'size'})
        sizes = [doc.file_object.size for doc in response.context['documents']]
        self.assertEqual(sizes, sorted(sizes))

    def test_sort_by_size_descending(self):
        response = self.client.get(reverse('catalog_root'), {'sort': '-size'})
        sizes = [doc.file_object.size for doc in response.context['documents']]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_invalid_sort_falls_back_to_default(self):
        response = self.client.get(reverse('catalog_root'), {'sort': 'not_a_real_field; DROP TABLE'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_sort'], '-uploaded_at')

    def test_default_sort_is_newest_first(self):
        response = self.client.get(reverse('catalog_root'))
        self.assertEqual(response.context['active_sort'], '-uploaded_at')
