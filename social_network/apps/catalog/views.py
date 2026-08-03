from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .models import CatalogDocument, CatalogFolder


class CatalogFolderView(LoginRequiredMixin, ListView):
    """Показывает содержимое папки (подпапки + документы). folder_id=None —
    корень каталога. Доступ — LoginRequiredMixin, без дополнительных прав
    (ARCHITECTURE.md: каталог общедоступен для всех сотрудников)."""

    template_name = 'catalog/folder.html'
    context_object_name = 'documents'
    paginate_by = 24

    def get_queryset(self):
        folder_id = self.kwargs.get('folder_id')
        documents = CatalogDocument.objects.filter(
            folder_id=folder_id, is_deleted=False
        ).select_related('file_object', 'file_object__blob', 'uploaded_by')

        query = self.request.GET.get('q', '').strip()
        if query:
            documents = documents.filter(title__icontains=query)
        return documents

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder_id = self.kwargs.get('folder_id')
        context['current_folder'] = (
            get_object_or_404(CatalogFolder, pk=folder_id) if folder_id else None
        )
        context['subfolders'] = CatalogFolder.objects.filter(parent_id=folder_id)
        context['all_folders'] = CatalogFolder.objects.all()
        context['trash_url'] = reverse('catalog_trash')
        return context


class CreateFolderView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent_id') or None

        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        folder = CatalogFolder.objects.create(name=name, parent_id=parent_id, created_by=request.user)
        return JsonResponse({'success': True, 'id': folder.pk, 'name': folder.name})


class UploadCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request):
        uploaded = request.FILES.get('file')
        title = request.POST.get('title', '').strip()
        folder_id = request.POST.get('folder_id') or None

        if not uploaded:
            return JsonResponse({'success': False, 'error': 'Файл не выбран'}, status=400)

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
        return JsonResponse({'success': True})


class DownloadCatalogDocumentView(LoginRequiredMixin, View):
    def get(self, request, doc_id):
        # Права тривиальны: любой аутентифицированный пользователь.
        # LoginRequiredMixin уже это обеспечивает — дополнительной проверки
        # здесь не требуется (ARCHITECTURE.md, раздел 8).
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=False)
        return StorageService.get_download_response(document.file_object, request)


class TrashCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=False)
        document.is_deleted = True
        document.deleted_at = timezone.now()
        document.deleted_by = request.user
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class RestoreCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=True)
        document.is_deleted = False
        document.deleted_at = None
        document.deleted_by = None
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class PurgeCatalogDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(CatalogDocument, pk=doc_id, is_deleted=True)
        # detach() вызывать не нужно — его выполнит сигнал post_delete;
        # пометка лишь сохраняет в журнале, кто инициировал удаление.
        attribute_deletion(document, user=request.user, consumer='catalog.CatalogDocument')
        document.delete()
        return JsonResponse({'success': True})


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
        context['trash_url'] = reverse('catalog_trash')
        return context
