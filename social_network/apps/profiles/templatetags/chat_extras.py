from django import template
from django.utils import timezone

register = template.Library()

MONTHS_GENITIVE = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]


@register.filter
def chat_date_label(value):
    """Возвращает 'Сегодня', 'Вчера' или дату в формате '23 июля 2026'."""
    if not value:
        return ''

    local_dt = timezone.localtime(value) if timezone.is_aware(value) else value
    message_date = local_dt.date()
    today = timezone.localdate()
    delta_days = (today - message_date).days

    if delta_days == 0:
        return 'Сегодня'
    if delta_days == 1:
        return 'Вчера'

    label = f'{message_date.day} {MONTHS_GENITIVE[message_date.month - 1]}'
    if message_date.year != today.year:
        label += f' {message_date.year}'
    return label
