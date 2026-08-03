from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .models import ExchangeFile


class ExchangeFolderListView(LoginRequiredMixin, ListView):
    """Корень обменника — список папок сотрудников. Папка = пользователь,
    отдельной модели папки нет (см. ExchangeFile)."""

    template_name = 'exchange/folders.html'
    context_object_name = 'folders'
    paginate_by = 24

    def get_queryset(self):
        query = self.request.GET.get('q', '').strip()

        users = get_user_model().objects.annotate(
            files_count=Count('exchange_files', filter=Q(exchange_files__is_deleted=False))
        ).order_by('last_name', 'first_name')

        if query:
            users = users.filter(
                Q(last_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(username__icontains=query)
            )
        return users

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trash_url'] = reverse('exchange_trash')
        return context


class ExchangeFolderView(LoginRequiredMixin, ListView):
    """Содержимое папки сотрудника. Видно всем — как в сетевой папке."""

    template_name = 'exchange/folder.html'
    context_object_name = 'files'
    paginate_by = 24

    def get_queryset(self):
        self.folder_owner = get_object_or_404(get_user_model(), pk=self.kwargs['user_id'])
        files = ExchangeFile.objects.filter(
            owner=self.folder_owner, is_deleted=False
        ).select_related('file_object', 'file_object__blob', 'uploaded_by')

        query = self.request.GET.get('q', '').strip()
        if query:
            files = files.filter(file_object__original_name__icontains=query)
        return files

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['folder_owner'] = self.folder_owner
        context['is_own_folder'] = self.folder_owner.pk == self.request.user.pk
        context['trash_url'] = reverse('exchange_trash')
        return context


class UploadExchangeFileView(LoginRequiredMixin, View):
    """Загрузка в папку любого сотрудника — как положить файл в чужую папку
    на сетевом диске."""

    def post(self, request, user_id):
        folder_owner = get_object_or_404(get_user_model(), pk=user_id)
        uploaded_files = request.FILES.getlist('files') or (
            [request.FILES['file']] if 'file' in request.FILES else []
        )

        if not uploaded_files:
            return JsonResponse({'success': False, 'error': 'Файл не выбран'}, status=400)

        created = 0
        for uploaded in uploaded_files:
            try:
                file_object = StorageService.upload(
                    uploaded, user=request.user, category=FileObject.Category.EXCHANGE,
                )
            except FileTooLargeError:
                return JsonResponse(
                    {'success': False, 'error': f'Файл «{uploaded.name}» слишком большой'}, status=400
                )
            except QuotaExceededError:
                return JsonResponse(
                    {'success': False, 'error': 'Превышена квота хранилища'}, status=400
                )

            ExchangeFile.objects.create(
                file_object=file_object, owner=folder_owner, uploaded_by=request.user,
            )
            created += 1

        return JsonResponse({'success': True, 'created': created})


class DownloadExchangeFileView(LoginRequiredMixin, View):
    """Скачивание доступно всем сотрудникам: содержимое папок обменника
    открыто, как в сетевой папке. Ограничение только на удаление."""

    def get(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)
        return StorageService.get_download_response(exchange_file.file_object, request)


class TrashExchangeFileView(LoginRequiredMixin, View):
    """Удаление в корзину — storage не трогается вообще."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)

        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        exchange_file.is_deleted = True
        exchange_file.deleted_at = timezone.now()
        exchange_file.deleted_by = request.user
        exchange_file.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class ExchangeTrashView(LoginRequiredMixin, ListView):
    """Корзина текущего пользователя: то, что он удалил сам, плюс удалённое
    из его папки другими."""

    template_name = 'exchange/trash.html'
    context_object_name = 'files'
    paginate_by = 24

    def get_queryset(self):
        return ExchangeFile.objects.filter(
            Q(deleted_by=self.request.user) | Q(owner=self.request.user),
            is_deleted=True,
        ).select_related('file_object', 'file_object__blob', 'uploaded_by').order_by('-deleted_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trash_url'] = reverse('exchange_trash')
        return context


class RestoreExchangeFileView(LoginRequiredMixin, View):
    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=True)

        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        exchange_file.is_deleted = False
        exchange_file.deleted_at = None
        exchange_file.deleted_by = None
        exchange_file.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        return JsonResponse({'success': True})


class PurgeExchangeFileView(LoginRequiredMixin, View):
    """Окончательное удаление — только из корзины. Здесь и только здесь
    вызывается StorageService.detach() (ARCHITECTURE.md, раздел 6)."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=True)

        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        # detach() вызывать не нужно — его выполнит сигнал post_delete;
        # пометка лишь сохраняет в журнале, кто инициировал удаление.
        attribute_deletion(exchange_file, user=request.user, consumer='exchange.ExchangeFile')
        exchange_file.delete()
        return JsonResponse({'success': True})
