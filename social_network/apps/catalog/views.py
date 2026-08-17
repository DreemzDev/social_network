"""Каталог — общий раздел организации.

Пишущие вьюхи прав не проверяют **намеренно**: переименовать, перенести и
удалить документ или папку может любой аутентифицированный. Это отличает
каталог от `exchange` и `deptdocs` и каждый аудит выглядит как пропущенная
проверка — обоснование в ARCHITECTURE.md, «Каталог открыт на запись».
Поведение закреплено тестом `CatalogIsOpenToEveryoneTest`.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from storage import realtime
from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.fmviews import (
    DownloadObjectView, PurgeObjectView, RenameObjectView, RestoreObjectView,
    TrashObjectView,
)
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion
from storage.utils import (
    PartialGridMixin,
    apply_filters,
    apply_sort,
    build_folder_choices,
    fm_archive_upload_view,
    fm_archive_view,
    fm_folder_archive_view,
    fm_task_response,
    folder_ancestors,
    folder_subtree,
    folder_subtree_ids,
    is_descendant,
)

from profiles.models import Notification
from profiles.views._common import notify

from .models import CatalogDocument, CatalogFolder


def _notify_folder(folder_id, *, actor, action, text):
    """Сообщает всем, кто сейчас смотрит эту папку каталога, что её
    содержимое изменилось (storage/realtime.py)."""
    realtime.broadcast(
        realtime.SCOPE_CATALOG, realtime.folder_location(folder_id),
        action=action, actor=actor, text=text,
    )


class CatalogDocumentMixin:
    """Каталог общий: `check_permission` намеренно не переопределяется —
    в базовом классе он и так ничего не проверяет."""

    model = CatalogDocument
    pk_kwarg = 'doc_id'
    noun = 'документ'

    def notify(self, obj, *, actor, action, text):
        _notify_folder(obj.folder_id, actor=actor, action=action, text=text)


class CatalogFolderView(PartialGridMixin, LoginRequiredMixin, ListView):
    """Показывает содержимое папки (подпапки + документы). folder_id=None —
    корень каталога. Доступ — LoginRequiredMixin, без дополнительных прав
    (ARCHITECTURE.md: каталог общедоступен для всех сотрудников)."""

    template_name = 'catalog/folder.html'
    partial_template_name = 'catalog/_grid.html'
    context_object_name = 'documents'

    def get_queryset(self):
        folder_id = self.kwargs.get('folder_id')
        documents = CatalogDocument.objects.filter(
            folder_id=folder_id, is_deleted=False
        ).select_related('file_object', 'file_object__blob', 'uploaded_by')

        documents, self.active_filters = apply_filters(
            documents, self.request.GET, name_field='title',
        )
        documents, self.active_sort = apply_sort(
            documents, self.request.GET.get('sort', ''), name_field='title',
        )
        return documents

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder_id = self.kwargs.get('folder_id')

        current_folder = get_object_or_404(CatalogFolder, pk=folder_id) if folder_id else None
        context['current_folder'] = current_folder
        context['folder_path'] = folder_ancestors(current_folder)
        context['subfolders'] = CatalogFolder.objects.filter(parent_id=folder_id)
        # Дерево с отступами вместо плоского списка: две папки «Приказы» в
        # разных разделах в плоском селекте выглядели одинаково.
        context['all_folders'] = build_folder_choices(CatalogFolder.objects.all())
        context['all_users'] = get_user_model().objects.order_by('last_name', 'first_name')
        context['trash_url'] = reverse('storage_trash')
        context.update(self.get_fm_context(self.active_sort, self.active_filters))
        return context


class CreateFolderView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent_id') or None

        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        # Существование родителя проверяется явно: раньше parent_id уходил
        # в create() как есть, и несуществующий id давал IntegrityError и
        # 500 вместо понятного ответа.
        parent = get_object_or_404(CatalogFolder, pk=parent_id) if parent_id else None

        folder = CatalogFolder.objects.create(name=name, parent=parent, created_by=request.user)

        _notify_folder(parent_id, actor=request.user,
                       action='folder_created', text=f'создал папку «{name}»')
        return JsonResponse({'success': True, 'id': folder.pk, 'name': folder.name})


class RenameCatalogFolderView(LoginRequiredMixin, View):
    """Права тривиальны, как и на весь каталог (ARCHITECTURE.md: доступен
    всем аутентифицированным) — переименовать может кто угодно."""

    def post(self, request, folder_id):
        folder = get_object_or_404(CatalogFolder, pk=folder_id)
        name = request.POST.get('name', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        folder.name = name
        folder.save(update_fields=['name'])

        _notify_folder(folder.parent_id, actor=request.user,
                       action='folder_renamed', text=f'переименовал папку в «{name}»')
        return JsonResponse({'success': True, 'name': folder.name})


class DeleteCatalogFolderView(LoginRequiredMixin, View):
    """Удаление папки каталога вместе с содержимым.

    Отдельной вьюхи раньше не было вообще: папку можно было создать,
    переименовать и перенести, но не удалить — убирать её приходилось
    через /admin/, где каскад сносил документы физически, мимо корзины.
    Здесь документы всего поддерева сначала переезжают в корзину, и лишь
    затем удаляется папка, как и при удалении документов по одному
    (ARCHITECTURE.md, раздел 6)."""

    def post(self, request, folder_id):
        folder = get_object_or_404(CatalogFolder, pk=folder_id)
        parent_id = folder.parent_id
        name = folder.name

        with transaction.atomic():
            subtree_ids = folder_subtree_ids(folder)
            # folder=None обязательно, а не только is_deleted=True:
            # CatalogDocument.folder — CASCADE, и без отвязки удаление
            # папки снесло бы документы физически, мимо корзины.
            # folder=None у каталога означает «в корне», туда документ и
            # вернётся при восстановлении.
            CatalogDocument.objects.filter(folder_id__in=subtree_ids, is_deleted=False).update(
                folder=None, is_deleted=True, deleted_at=timezone.now(), deleted_by=request.user,
            )
            CatalogDocument.objects.filter(folder_id__in=subtree_ids, is_deleted=True).update(folder=None)
            folder.delete()

        _notify_folder(parent_id, actor=request.user,
                       action='folder_deleted', text=f'удалил папку «{name}»')
        return JsonResponse({'success': True})


class MoveCatalogFolderView(LoginRequiredMixin, View):
    """Перенос папки в другую (смена parent). Папка не может стать
    потомком самой себя или собственного потомка — иначе дерево
    self-FK порвётся в цикл, и обход subfolders зациклится."""

    def post(self, request, folder_id):
        folder = get_object_or_404(CatalogFolder, pk=folder_id)
        new_parent_id = request.POST.get('parent_id') or None
        old_parent_id = folder.parent_id

        if new_parent_id:
            new_parent = get_object_or_404(CatalogFolder, pk=new_parent_id)
            if new_parent.pk == folder.pk or is_descendant(new_parent, folder):
                return JsonResponse(
                    {'success': False, 'error': 'Нельзя перенести папку в саму себя или во вложенную папку'},
                    status=400,
                )

        folder.parent_id = new_parent_id
        folder.save(update_fields=['parent'])

        _notify_folder(old_parent_id, actor=request.user,
                       action='folder_moved', text=f'перенёс папку «{folder.name}»')
        if str(old_parent_id) != str(new_parent_id):
            _notify_folder(new_parent_id, actor=request.user,
                           action='folder_moved', text=f'перенёс сюда папку «{folder.name}»')
        return JsonResponse({'success': True})



class RenameCatalogDocumentView(CatalogDocumentMixin, RenameObjectView):
    pass


class MoveCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=False)
        folder_id = request.POST.get('folder_id') or None
        source_folder_id = document.folder_id

        if folder_id:
            get_object_or_404(CatalogFolder, pk=folder_id)

        document.folder_id = folder_id
        document.save(update_fields=['folder'])

        _notify_folder(source_folder_id, actor=request.user,
                       action='file_moved', text='переместил документ')
        if str(source_folder_id) != str(folder_id):
            _notify_folder(folder_id, actor=request.user,
                           action='file_moved', text='переместил сюда документ')
        return JsonResponse({'success': True})


class BulkDownloadCatalogDocumentsView(LoginRequiredMixin, View):
    """Скачать выбранные документы одним архивом.

    Права тривиальны и совпадают с одиночным скачиванием: каталог
    общедоступен всем аутентифицированным (ARCHITECTURE.md, раздел 8),
    поэтому фильтр — только is_deleted=False.
    """

    def get(self, request):
        return fm_archive_view(
            request,
            CatalogDocument.objects.filter(is_deleted=False),
            filename='Информационный каталог.zip',
        )


class DownloadCatalogFolderView(LoginRequiredMixin, View):
    """Скачать папку целиком одним архивом, вместе с вложенными.

    Права те же, что и на весь каталог: он общедоступен всем
    аутентифицированным, поэтому сужать поддерево не по чему.
    """

    def get(self, request, folder_id):
        folder = get_object_or_404(CatalogFolder, pk=folder_id)
        paths = folder_subtree(folder)

        documents = CatalogDocument.objects.filter(
            folder_id__in=paths, is_deleted=False,
        ).select_related('file_object__blob')

        return fm_folder_archive_view(
            request,
            items=[(paths[document.folder_id], document.file_object) for document in documents],
            filename=f'{folder.name}.zip',
        )


class BulkMoveCatalogDocumentsView(LoginRequiredMixin, View):
    """Массовое перемещение выбранных документов в другую папку — уходит в
    Celery (storage.tasks.bulk_move_documents): при выборе сотен документов
    (вся папка целиком) синхронный queryset.update() держал бы HTTP-запрос
    без обратной связи до самого конца. Фронт получает task_id и опрашивает
    /storage/task-status/ (см. storage.views.TaskStatusView)."""

    def post(self, request):
        doc_ids = request.POST.getlist('doc_ids')
        folder_id = request.POST.get('folder_id') or None

        if not doc_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)
        if folder_id:
            get_object_or_404(CatalogFolder, pk=folder_id)

        source_folder_ids = set(
            CatalogDocument.objects.filter(pk__in=doc_ids).values_list('folder_id', flat=True)
        )

        from storage.tasks import bulk_move_documents

        task = bulk_move_documents.delay(
            'catalog', 'CatalogDocument', doc_ids, 'folder_id', folder_id,
        )

        for source_id in source_folder_ids | {folder_id}:
            _notify_folder(source_id, actor=request.user,
                           action='files_moved', text='переместил документы')
        return fm_task_response(request, task)


class BulkTrashCatalogDocumentsView(LoginRequiredMixin, View):
    """Массовое удаление выбранных документов в корзину — уходит в Celery
    (storage.tasks.bulk_trash_documents), тот же принцип, что и у переноса
    выше. Это флаг is_deleted, а не физическое удаление, detach() здесь ни
    при чём (ARCHITECTURE.md, раздел 6)."""

    def post(self, request):
        doc_ids = request.POST.getlist('doc_ids')
        if not doc_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)

        folder_ids = set(
            CatalogDocument.objects.filter(pk__in=doc_ids).values_list('folder_id', flat=True)
        )

        from storage.tasks import bulk_trash_documents

        task = bulk_trash_documents.delay('catalog', 'CatalogDocument', doc_ids, request.user.pk)

        for folder_id in folder_ids:
            _notify_folder(folder_id, actor=request.user,
                           action='files_trashed', text='удалил документы в корзину')
        return fm_task_response(request, task)


class UploadCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request):
        uploaded = request.FILES.get('file')
        title = request.POST.get('title', '').strip()
        folder_id = request.POST.get('folder_id') or None

        if not uploaded:
            return JsonResponse({'success': False, 'error': 'Файл не выбран'}, status=400)

        if folder_id:
            get_object_or_404(CatalogFolder, pk=folder_id)

        try:
            file_object = StorageService.upload(
                uploaded, user=request.user, category=FileObject.Category.CATALOG,
            )
        except FileTooLargeError:
            return JsonResponse({'success': False, 'error': 'Файл слишком большой'}, status=400)
        except QuotaExceededError:
            return JsonResponse({'success': False, 'error': 'Превышена квота хранилища'}, status=400)

        CatalogDocument.objects.create(
            folder_id=folder_id,
            file_object=file_object,
            title=title or uploaded.name,
            uploaded_by=request.user,
        )

        _notify_folder(folder_id, actor=request.user,
                       action='file_created', text='добавил документ')
        return JsonResponse({'success': True})


class UploadCatalogArchiveView(LoginRequiredMixin, View):
    """Загрузить zip и распаковать его в текущую папку каталога.

    Отдельная кнопка, а не «если загрузили .zip — распаковать»: архив может
    быть и обычным документом, который нужен на портале как есть
    (например, комплект чертежей одним файлом). Догадываться за
    пользователя тут нельзя — он должен сказать, чего хочет.
    """

    def post(self, request):
        folder_id = request.POST.get('folder_id') or None
        if folder_id:
            get_object_or_404(CatalogFolder, pk=folder_id)

        from .tasks import import_catalog_archive

        return fm_archive_upload_view(
            request,
            category=FileObject.Category.CATALOG,
            launch=lambda archive: import_catalog_archive.delay(
                archive.pk, folder_id, request.user.pk,
            ),
        )


class SendCatalogDocumentToExchangeView(LoginRequiredMixin, View):
    """Переслать документ каталога в обменник — коллеге или себе. Новый
    FileObject на тот же blob (StorageService.copy_reference), без
    повторной загрузки содержимого: файл уже лежит на диске под своим
    checksum (ARCHITECTURE.md, раздел 5, copy_reference)."""

    def post(self, request, doc_id):
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=False)
        recipient_id = request.POST.get('recipient_id')

        if not recipient_id:
            return JsonResponse({'success': False, 'error': 'Выберите получателя'}, status=400)

        recipient = get_object_or_404(get_user_model(), pk=recipient_id)

        try:
            copied_object = StorageService.copy_reference(
                document.file_object, user=request.user, category=FileObject.Category.EXCHANGE,
            )
        except QuotaExceededError:
            return JsonResponse({'success': False, 'error': 'Превышена квота хранилища'}, status=400)

        from exchange.models import ExchangeFile

        ExchangeFile.objects.create(
            file_object=copied_object, owner=recipient, uploaded_by=request.user,
        )

        if recipient.pk != request.user.pk:
            notify(
                recipient, Notification.Kind.FILE_SHARED,
                f'{request.user.get_full_name() or request.user.username} переслал вам документ '
                f'«{document.title}» из каталога',
                actor=request.user, url=reverse('exchange_folder', args=[recipient.pk]),
            )

        realtime.broadcast(
            realtime.SCOPE_EXCHANGE, realtime.exchange_location(recipient.pk, None),
            action='file_created', actor=request.user, text='переслал сюда документ из каталога',
        )
        return JsonResponse({'success': True})


class TrashCatalogDocumentView(CatalogDocumentMixin, TrashObjectView):
    pass


class RestoreCatalogDocumentView(CatalogDocumentMixin, RestoreObjectView):
    pass


class PurgeCatalogDocumentView(CatalogDocumentMixin, PurgeObjectView):
    pass


class DownloadCatalogDocumentView(CatalogDocumentMixin, DownloadObjectView):
    """`?inline=1` — открыть в браузере вместо скачивания. Права тривиальны:
    каталог виден всем аутентифицированным (ARCHITECTURE.md, раздел 8)."""


class CatalogTrashView(LoginRequiredMixin, ListView):
    template_name = 'catalog/trash.html'
    context_object_name = 'documents'
    paginate_by = 24

    def get_queryset(self):
        return CatalogDocument.objects.filter(is_deleted=True).select_related(
            'file_object', 'file_object__blob', 'uploaded_by'
        ).order_by('-deleted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trash_url'] = reverse('storage_trash')
        return context
