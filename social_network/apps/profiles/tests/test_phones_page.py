"""Справочник организации — страница устроена как «Коллеги».

Обе страницы делят фильтр отделов (`dept_links` + один партиал) и скрипт
подгрузки по прокрутке. Тесты стерегут именно стыки: если справочник
перестанет отдавать фрагмент на XHR или разойдётся с «Коллегами» по форме
`dept_links`, общий партиал и общий скрипт молча перестанут работать —
страница при этом отрисуется без единой ошибки.
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from profiles.models import Category

User = get_user_model()


class PhonesPageTest(TestCase):

    def setUp(self):
        Category.objects.all().delete()
        self.sales = Category.objects.create(name='Отдел продаж')
        self.tech = Category.objects.create(name='Технический отдел')

        self.user = User.objects.create_user(username='phones_viewer', password='pass12345')
        User.objects.create_user(
            username='seller', password='pass12345',
            first_name='Иван', last_name='Иванов', cat=self.sales,
        )
        User.objects.create_user(
            username='techie', password='pass12345',
            first_name='Пётр', last_name='Петров', cat=self.tech,
        )
        self.client.force_login(self.user)

    def test_page_renders_cards_and_dept_filter(self):
        response = self.client.get(reverse('show_phones'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/phones/card.html')
        self.assertTemplateUsed(response, 'includes/colleagues/filter_dept.html')
        self.assertContains(response, 'Отдел продаж')

    def test_dept_filter_narrows_the_list(self):
        response = self.client.get(reverse('show_phones'), {'dept': 'Отдел продаж'})

        usernames = [u.username for u in response.context['phones']]
        self.assertIn('seller', usernames)
        self.assertNotIn('techie', usernames)

    def test_search_narrows_the_list(self):
        response = self.client.get(reverse('show_phones'), {'q': 'Петров'})

        usernames = [u.username for u in response.context['phones']]
        self.assertEqual(usernames, ['techie'])

    def test_xhr_returns_fragment_for_infinite_scroll(self):
        """Подгрузка по прокрутке ждёт JSON с готовой разметкой.

        Скрипт `infinite-list.js` общий с «Коллегами» и ничего не знает про
        конкретную страницу: отдай справочник обычный HTML — прокрутка
        молча перестала бы догружать, без ошибки в консоли.
        """
        response = self.client.get(
            reverse('show_phones'), HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response['Content-Type'], 'application/json')
        payload = json.loads(response.content)
        self.assertIn('html', payload)
        self.assertIn('has_next', payload)
        self.assertIn('Иванов', payload['html'])

    def test_dept_links_have_the_same_shape_as_colleagues(self):
        """Партиал фильтра общий, значит и структура ссылок обязана совпадать."""
        phones = self.client.get(reverse('show_phones')).context['dept_links']
        colleagues = self.client.get(reverse('show_users')).context['dept_links']

        self.assertEqual([sorted(link) for link in phones], [sorted(link) for link in colleagues])
        self.assertEqual(phones[0]['label'], 'Все отделы')
        self.assertTrue(phones[0]['active'], 'без ?dept активен пункт «Все отделы»')

    def test_legacy_filter_url_still_works(self):
        """Старый адрес /filterPhones/<id>/ остаётся рабочим: боковой фильтр
        давно ходит через ?dept=, но на прежний адрес могли остаться ссылки."""
        response = self.client.get(reverse('filterPhones', args=[self.tech.pk]))

        usernames = [u.username for u in response.context['phones']]
        self.assertEqual(usernames, ['techie'])
