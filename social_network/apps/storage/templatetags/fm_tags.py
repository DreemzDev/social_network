"""Шаблонные теги файлового менеджера."""
from django import template
from django.conf import settings
from django.utils import timezone

from ..utils import PREVIEWABLE_EXTENSIONS

register = template.Library()


@register.simple_tag(takes_context=True)
def fm_qs(context, **overrides):
    """Query-string с сохранением текущих параметров списка.

    Переносит всё, кроме служебного 'partial' и явно переопределённого, —
    ссылки, собранные руками, теряли ?sort= и ?per_page= при переходе на
    вторую страницу.

    {% fm_qs page=2 %}, {% fm_qs sort='name' page=None %} (None удаляет).
    """
    params = context['request'].GET.copy()
    params.pop('partial', None)

    for key, value in overrides.items():
        if value is None or value == '':
            params.pop(key, None)
        else:
            params[key] = value

    encoded = params.urlencode()
    return f'?{encoded}' if encoded else ''


@register.filter
def is_previewable(extension) -> bool:
    """Можно ли осмысленно открыть файл в браузере (?inline=1): пункт
    «Просмотр» не должен обещать то, чего по клику не произойдёт."""
    return str(extension or '').upper() in PREVIEWABLE_EXTENSIONS


@register.filter
def fm_can_manage(item, user) -> bool:
    """Может ли пользователь переименовывать / перемещать / удалять объект.

    Фильтр, а не свойство модели: решение зависит от текущего пользователя, а
    {% if %} не умеет звать методы с аргументами. Политики остаются в моделях
    (exchange — `can_be_deleted_by`, deptdocs — `is_accessible_by`, catalog —
    общедоступен), здесь только выбор нужной.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False

    for method_name in ('can_be_deleted_by', 'is_accessible_by'):
        method = getattr(item, method_name, None)
        if callable(method):
            return bool(method(user))

    return True


@register.filter
def trash_days_left(deleted_at) -> int | None:
    """Сколько дней осталось до автоочистки корзины."""
    if not deleted_at:
        return None

    retention = getattr(settings, 'STORAGE_TRASH_RETENTION_DAYS', 30)
    days_passed = (timezone.now() - deleted_at).days
    return max(retention - days_passed, 0)
