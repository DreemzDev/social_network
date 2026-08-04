"""Шаблонные теги файлового менеджера."""
from django import template
from django.conf import settings
from django.utils import timezone

from ..utils import PREVIEWABLE_EXTENSIONS

register = template.Library()


@register.simple_tag(takes_context=True)
def fm_qs(context, **overrides):
    """Строит query-string, СОХРАНЯЯ текущие параметры списка и заменяя
    только переданные.

    Написан потому, что пагинация и сортировка руками собирали ссылки вида
    '?page=2{% if request.GET.q %}&q={{ request.GET.q }}{% endif %}' — то
    есть переносили только поиск. Переход на вторую страницу молча терял
    ?sort= и возвращал список к сортировке по умолчанию, а после появления
    ?per_page= и полей расширенного фильтра терялись бы и они. Здесь
    переносится всё, кроме служебного 'partial' и явно переопределённого.

    Использование: {% fm_qs page=2 %}, {% fm_qs sort='name' page=None %}
    (None удаляет параметр).
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
    """Можно ли осмысленно открыть файл прямо в браузере (?inline=1).

    Пункт «Просмотр» раньше показывался для любого файла — для .docx или
    .zip браузер всё равно скачивал файл или показывал бинарный мусор,
    то есть меню обещало не то, что произойдёт по клику."""
    return str(extension or '').upper() in PREVIEWABLE_EXTENSIONS


@register.filter
def fm_can_manage(item, user) -> bool:
    """Может ли пользователь переименовывать / перемещать / удалять объект.

    Шаблонный фильтр, а не свойство модели, потому что решение зависит от
    текущего пользователя, а {% if %} не умеет вызывать методы с
    аргументами. Раньше эта же проверка стояла в карточках копипастой
    ({% if item.owner_id == request.user.pk or item.uploaded_by_id == ... %})
    и повторяла условие, которое на сервере уже реализовано методом модели —
    два источника истины про одно правило.

    Политики трёх модулей разные и намеренно остаются в своих моделях:
      exchange — can_be_deleted_by(): владелец папки или загрузивший;
      deptdocs — is_accessible_by():  участник allowed_users папки;
      catalog  — метода нет: каталог общедоступен всем аутентифицированным.
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
    """Сколько дней осталось до автоматического окончательного удаления.

    В корзине это главное, чего не хватало: срок автоочистки существует
    (STORAGE_TRASH_RETENTION_DAYS), но на карточке его не было видно, и
    понять, горит ли восстановление, было нельзя."""
    if not deleted_at:
        return None

    retention = getattr(settings, 'STORAGE_TRASH_RETENTION_DAYS', 30)
    days_passed = (timezone.now() - deleted_at).days
    return max(retention - days_passed, 0)
