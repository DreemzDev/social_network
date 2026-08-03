from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .models import DepartmentDocument, DepartmentFolder


def _visible_root_folders(user):
    """Папки верхнего уровня, доступные пользователю: он либо в
    allowed_users самой папки, либо в allowed_users любого предка (доступ к
    родителю не сужается детьми — так же, как обычные права на директорию)."""
    return DepartmentFolder.objects.filter(
        parent__isnull=True, allowed_users=user
    ).distinct()


class DepartmentFolderListView(LoginRequiredMixin, ListView):
    """Содержимое папки документов отдела (подпапки + документы).
    folder_id=None — папки верхнего уровня, доступные пользователю."""

    template_name = 'deptdocs/list.html'
    context_object_name = 'documents'
    paginate_by = 24

    def get_queryset(self):
        folder_id = self.kwargs.get('folder_id')

        if folder_id:
            self.current_folder = get_object_or_404(DepartmentFolder, pk=folder_id)
            if not self.current_folder.is_accessible_by(self.request.user):
                raise PermissionDenied
        else:
            self.current_folder = None

        documents = DepartmentDocument.objects.filter(
            folder_id=folder_id, is_deleted=False
        ).select_related('file_object', 'file_object__blob', 'uploaded_by')

        query = self.request.GET.get('q', '').strip()
        if query:
            documents = documents.filter(title__icontains=query)
        return documents

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder_id = self.kwargs.get('folder_id')

        context['current_folder'] = self.current_folder
        context['can_upload'] = self.current_folder is not None

        if self.current_folder:
            context['subfolders'] = DepartmentFolder.objects.filter(parent=self.current_folder)
        else:
            context['subfolders'] = _visible_root_folders(self.request.user)

        context['trash_url'] = reverse('deptdocs_trash')
        context['all_users'] = get_user_model().objects.exclude(pk=self.request.user.pk).order_by(
            'last_name', 'first_name'
        )
        return context


class CreateDepartmentFolderView(LoginRequiredMixin, View):
    """Создать может любой сотрудник. Создатель сам решает, кому дать доступ
    (allowed_users) — по умолчанию сам себе, иначе бы сразу потерял доступ к
    только что созданной папке."""

    def post(self, request):
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent_id') or None
        user_ids = request.POST.getlist('user_ids')

        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        if parent_id:
            parent = get_object_or_404(DepartmentFolder, pk=parent_id)
            if not parent.is_accessible_by(request.user):
                raise PermissionDenied

        folder = DepartmentFolder.objects.create(name=name, parent_id=parent_id, created_by=request.user)

        user_ids = set(user_ids)
        user_ids.add(str(request.user.pk))
        folder.allowed_users.set(user_ids)

        return JsonResponse({'success': True, 'id': folder.pk})


class UpdateFolderAccessView(LoginRequiredMixin, View):
    """Изменить список доступа может любой текущий участник — включая
    исключение самого себя (после чего он теряет доступ к папке)."""

    def post(self, request, folder_id):
        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        user_ids = request.POST.getlist('user_ids')
        if not user_ids:
            return JsonResponse({'success': False, 'error': 'Список доступа не может быть пустым'}, status=400)

        folder.allowed_users.set(user_ids)
        return JsonResponse({'success': True, 'lost_access': str(request.user.pk) not in user_ids})


class UploadDepartmentDocumentView(LoginRequiredMixin, View):
    """Загрузка возможна только внутрь папки — вне папки документ не имеет
    прав и не был бы виден никому. Доступ проверяется по самой папке."""

    def post(self, request):
        folder_id = request.POST.get('folder_id')
        if not folder_id:
            return JsonResponse({'success': False, 'error': 'Выберите папку для загрузки'}, status=400)

        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        uploaded = request.FILES.get('file')
        title = request.POST.get('title', '').strip()

        if not uploaded:
            return JsonResponse({'success': False, 'error': 'Файл не выбран'}, status=400)

        try:
            file_object = StorageService.upload(
                uploaded, user=request.user, category=FileObject.Category.DOCUMENT,
            )
        except FileTooLargeError:
            return JsonResponse({'success': False, 'error': 'Файл слишком большой'}, status=400)
        except QuotaExceededError:
            return JsonResponse({'success': False, 'error': 'Превышена квота хранилища'}, status=400)

        DepartmentDocument.objects.create(
            folder=folder, file_object=file_object, title=title or uploaded.name, uploaded_by=request.user,
        )
        return JsonResponse({'success': True})


class DownloadDepartmentDocumentView(LoginRequiredMixin, View):
    def get(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)

        # Права — здесь, не в storage (ARCHITECTURE.md, раздел 8).
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        return StorageService.get_download_response(document.file_object, request)


class TrashDepartmentDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        document.is_deleted = True
        document.deleted_at = timezone.now()
        document.deleted_by = request.user
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class RestoreDepartmentDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=True)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        document.is_deleted = False
        document.deleted_at = None
        document.deleted_by = None
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class PurgeDepartmentDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=True)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        # detach() вызывать не нужно — его выполнит сигнал post_delete;
        # пометка лишь сохраняет в журнале, кто инициировал удаление.
        attribute_deletion(document, user=request.user, consumer='deptdocs.DepartmentDocument')
        document.delete()
        return JsonResponse({'success': True})


class DepartmentDocumentTrashView(LoginRequiredMixin, ListView):
    """Корзина: документы из папок, доступных текущему пользователю."""

    template_name = 'deptdocs/trash.html'
    context_object_name = 'documents'
    paginate_by = 24

    def get_queryset(self):
        accessible_folder_ids = DepartmentFolder.objects.filter(
            allowed_users=self.request.user
        ).values_list('pk', flat=True)

        return DepartmentDocument.objects.filter(
            is_deleted=True, folder_id__in=accessible_folder_ids,
        ).select_related('file_object', 'file_object__blob', 'uploaded_by').order_by('-deleted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trash_url'] = reverse('deptdocs_trash')
        return context
