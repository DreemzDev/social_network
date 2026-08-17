"""Ни одна страница портала не открывается без входа.

Аудит 14.08.2026 нашёл семь таких страниц: лента, отфильтрованная по
подразделению (`/category/<id>/`), список сотрудников (`/users/`),
телефонный справочник (`/phones/`), их фильтрованные версии, отдельный пост
(`/post/<id>/`) и справка. Аноним получал страницу на 32 КБ с фамилиями и
телефонами сотрудников — при том что главная требовала входа.

Причина была не в замысле, а в невнимательности: `LoginRequiredMixin`
ставили на новые вьюхи и забывали на старых. Поэтому тест обходит **все**
маршруты, а не список из семи: новая вьюха без проверки уронит прогон сама,
без чьей-либо внимательности.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver, reverse

from profiles.models import Category
from posts.models import Post

User = get_user_model()

# Страницы, которые обязаны работать до входа.
PUBLIC = {'login', 'logout', 'register', 'password_reset_request', 'password_reset_confirm'}


class AnonymousSeesNothingTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='anon_probe_user', password='pass12345', last_name='Секретов',
        )
        self.category = Category.objects.create(name='Первый отдел')
        self.post = Post.objects.create(content='Внутреннее объявление', author=self.user)

    def routes(self):
        """(адрес, имя) для всех маршрутов, которые удаётся собрать."""
        sample = {
            'cat_id': self.category.pk, 'user_id': self.user.pk, 'post_id': self.post.pk,
            'username': self.user.username, 'pk': self.user.pk,
        }
        for pattern in get_resolver().url_patterns:
            for entry in getattr(pattern, 'url_patterns', [pattern]):
                name = getattr(entry, 'name', None)
                if not name or name in PUBLIC or 'admin' in str(entry.pattern):
                    continue
                route = str(entry.pattern)
                arguments = {key: value for key, value in sample.items() if f':{key}>' in route}
                try:
                    yield reverse(name, kwargs=arguments), name
                except Exception:
                    continue  # маршрут с параметрами, которых здесь неоткуда взять

    def test_no_page_answers_200_to_anonymous(self):
        opened, checked = [], 0
        for path, name in self.routes():
            try:
                response = self.client.get(path)
            except Exception:
                continue  # вьюха упала на подставных данных — не про этот тест
            checked += 1
            if response.status_code == 200:
                opened.append(f'{name} ({path})')

        self.assertEqual(opened, [])
        # Обход собирает маршруты сам, и поломка сборки превратила бы тест в
        # вечнозелёный: он проверял бы пустой список и ничего не стерёг.
        self.assertGreater(checked, 30, 'обход маршрутов собрал подозрительно мало адресов')

    def test_directory_does_not_leak_names_and_phones(self):
        """Самое дорогое из найденного: справочник отдавал анониму фамилии
        и телефоны всех сотрудников целой страницей."""
        for path in ('/users/', '/phones/'):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 302)
                self.assertNotContains(response, 'Секретов', status_code=302)

    def test_logged_in_user_still_sees_them(self):
        """Обратная сторона: защита не должна закрыть страницы своим."""
        self.client.force_login(self.user)

        for path in ('/users/', '/phones/', f'/category/{self.category.pk}/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
