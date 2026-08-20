"""Поиск по информационному каталогу.

Каталог открыт всем аутентифицированным на чтение и запись — намеренно
(ARCHITECTURE.md, «Каталог открыт на запись»), поэтому ограничение здесь
только одно: корзина не ищется.
"""
from django.db.models import Q
from django.urls import reverse

from .models import CatalogDocument


def search(query, user, limit):
    documents = CatalogDocument.objects.filter(is_deleted=False).filter(
        Q(title__icontains=query) | Q(file_object__original_name__icontains=query)
    ).select_related('file_object', 'folder')[:limit]

    return [{
        'title': item.title or item.file_object.original_name,
        'subtitle': f'Каталог · {item.folder.name}' if item.folder_id else 'Каталог',
        'url': reverse('catalog_folder', args=[item.folder_id]) if item.folder_id else reverse('catalog_root'),
    } for item in documents]
