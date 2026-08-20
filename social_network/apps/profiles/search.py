"""Общий поиск по порталу: сотрудники, файлы, справочники, новости, сеть.

Собирает результат из модулей, но сам про права ничего не знает: каждый
модуль отдаёт только то, что видно этому пользователю (см. деликатный
случай в deptdocs/search.py). Иначе поиск стал бы обходным путём к чужим
документам — заголовки утекали бы, даже если сам файл не скачать.

Группы перечислены явным списком, а не реестром с саморегистрацией: их
шесть, и явный список читается лучше, чем машинерия ради него.
"""
from django.db.models import Q
from django.urls import reverse

#: Сколько результатов показывать в каждой группе выпадающего списка.
GROUP_LIMIT = 5

#: Короче — не ищем: одна-две буквы совпадут почти со всем и дадут мусор
#: вместо ответа, а по каждому запросу это шесть обращений к БД.
MIN_QUERY_LENGTH = 2


def search_users(query, user, limit):
    """Сотрудники. Видны всем аутентифицированным — как страница «Коллеги»."""
    from django.contrib.auth import get_user_model

    people = get_user_model().objects.filter(
        Q(last_name__icontains=query) | Q(first_name__icontains=query)
        | Q(position__name__icontains=query) | Q(cat__name__icontains=query)
        | Q(cab__icontains=query) | Q(phones__number__icontains=query)
    ).select_related('position', 'cat').distinct()[:limit]

    return [{
        'title': f'{person.last_name} {person.first_name}'.strip() or person.username,
        'subtitle': ' · '.join(filter(None, [
            person.position.name if person.position_id else '',
            f'каб. {person.cab}' if person.cab else '',
        ])) or 'Сотрудник',
        'url': reverse('addpost', args=[person.username]),
        'avatar': person.avatar.url if person.avatar else '',
    } for person in people]


def search_everything(query, user, limit=GROUP_LIMIT):
    """Результат для выпадающего списка: [{title, icon, hits}, ...].

    Группа «Файлы» объединяет обменник, каталог и документы отделов: человек
    ищет файл, а не модуль, в котором тот лежит. Откуда файл — написано
    подписью под названием.
    """
    query = (query or '').strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []

    from catalog import search as catalog_search
    from deptdocs import search as deptdocs_search
    from exchange import search as exchange_search
    from netmap import search as netmap_search
    from phonebook import search as phonebook_search
    from posts import search as posts_search

    files = []
    for module in (exchange_search, catalog_search, deptdocs_search):
        files.extend(module.search(query, user, limit))

    groups = [
        ('Сотрудники', 'users', search_users(query, user, limit)),
        ('Файлы', 'file-text', files[:limit]),
        ('Справочники', 'book-open', phonebook_search.search(query, user, limit)),
        ('Новости', 'message-square', posts_search.search(query, user, limit)),
        ('Карта сети', 'server', netmap_search.search(query, user, limit)),
    ]

    return [
        {'title': title, 'icon': icon, 'hits': hits}
        for title, icon, hits in groups if hits
    ]
