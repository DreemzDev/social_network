"""Список справочников для подменю в боковом меню.

Меню рисуется на каждой странице, поэтому запрос лёгкий: только id и
название, только неудалённые. Анониму меню не показывается вовсе — для него
запроса не делаем.
"""
from .models import Phonebook

#: На каких страницах подменю должно быть раскрыто сразу — иначе активный
#: пункт оказывается спрятан внутри свёрнутого списка.
MENU_URL_NAMES = frozenset({'show_phones', 'filterPhones', 'phonebook', 'phonebook_add'})


def reference_books(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}

    match = getattr(request, 'resolver_match', None)
    return {
        'menu_books': Phonebook.objects.filter(is_deleted=False).only('id', 'title'),
        'phonebook_menu_open': bool(match and match.url_name in MENU_URL_NAMES),
    }
