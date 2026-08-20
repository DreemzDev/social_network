"""Поиск по документам отделов — самое ответственное место общего поиска.

Права здесь не «открыто всем», а список допущенных на папке
(folder.allowed_users). Поиск обязан спрашивать про них так же, как страница
папки: иначе он показывал бы названия документов тем, кому сама папка
недоступна, — то есть был бы утечкой через заголовки.

Документ без папки не наследует ничьих прав и не виден никому (см.
докстринг DepartmentDocument) — из выдачи он тоже исключён.
"""
from django.db.models import Q
from django.urls import reverse

from .models import DepartmentDocument


def search(query, user, limit):
    documents = DepartmentDocument.objects.filter(
        is_deleted=False,
        folder__isnull=False,
        folder__allowed_users=user,
    ).filter(
        Q(title__icontains=query) | Q(file_object__original_name__icontains=query)
    ).select_related('file_object', 'folder')[:limit]

    return [{
        'title': item.title or item.file_object.original_name,
        'subtitle': f'Документы отдела · {item.folder.name}',
        'url': reverse('deptdocs_folder', args=[item.folder_id]),
    } for item in documents]
