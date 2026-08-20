"""Поиск по файлам обменника.

Живёт в модуле-потребителе, а не в общем поиске: права на файлы знает
только он сам (ARCHITECTURE.md, раздел 8). Здесь ограничение простое —
содержимое обменника видно всем сотрудникам, кроме корзины.
"""
from django.urls import reverse

from .models import ExchangeFile


def search(query, user, limit):
    files = ExchangeFile.objects.filter(
        is_deleted=False, file_object__original_name__icontains=query,
    ).select_related('file_object', 'owner', 'folder')[:limit]

    return [{
        'title': item.file_object.original_name,
        'subtitle': f'Обменник · папка {item.owner.last_name} {item.owner.first_name}'.strip(),
        'url': _folder_url(item),
    } for item in files]


def _folder_url(item) -> str:
    """Ссылка ведёт в папку: своей страницы у файла нет, а в папке его видно
    вместе со всеми действиями."""
    if item.folder_id:
        return reverse('exchange_subfolder', args=[item.owner_id, item.folder_id])
    return reverse('exchange_folder', args=[item.owner_id])
