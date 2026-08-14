from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from storage import realtime
from storage.exceptions import FileTooLargeError, QuotaExceededError
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
)

from profiles.models import Notification
from profiles.views._common import notify

from .models import DepartmentDocument, DepartmentFolder


def _notify_folder(folder_id, *, actor, action, text):
    """Сообщает всем, кто сейчас смотрит эту папку, что её содержимое
    изменилось. Подписка на группу папки разрешается только участникам
    allowed_users — проверка при подключении в storage/consumers.py."""
    realtime.broadcast(
        realtime.SCOPE_DEPTDOCS, realtime.folder_location(folder_id),
        action=action, actor=actor, text=text,
    )


def _visible_root_folders(user):
    """Папки верхнего уровня, доступные пользователю.

    Доступ выдаётся на конкретную папку (allowed_users), а не наследуется
    от предков: подпапка может быть открыта другому кругу лиц, чем её
    родитель. Ранняя редакция докстринга обещала наследование от предков,
    но код его никогда не делал — обещание убрано, чтобы описание не
    расходилось с поведением."""
    return DepartmentFolder.objects.filter(
        parent__isnull=True, allowed_users=user
    ).distinct()


class DepartmentFolderListView(PartialGridMixin, LoginRequiredMixin, ListView):
    """Содержимое папки документов отдела (подпапки + документы).
    folder_id=None — папки верхнего уровня, доступные пользователю."""

    template_name = 'deptdocs/list.html'
    partial_template_name = 'deptdocs/_grid.html'
    context_object_name = 'documents'

    def get_queryset(self):
        folder_id = self.kwargs.get('folder_id')

        if folder_id:
            self.current_folder = get_object_or_404(DepartmentFolder, pk=folder_id)
            if not self.current_folder.is_accessible_by(self.request.user):
                raise PermissionDenied
        else:
            self.current_folder = None

        # Документ вне папки не наследует ничьих прав и не виден никому
        # (DepartmentDocument docstring), поэтому в корне списка документов
        # нет вообще — раньше корень показывал их всем подряд.
        if self.current_folder is None:
            documents = DepartmentDocument.objects.none()
            self.active_filters = {}
            self.active_sort = ''
            return documents

        documents = DepartmentDocument.objects.filter(
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

        context['current_folder'] = self.current_folder
        context['folder_path'] = folder_ancestors(self.current_folder)
        context['can_upload'] = self.current_folder is not None

        if self.current_folder:
            # Фильтр по доступу обязателен и здесь, а не только в корне:
            # без него, зайдя в доступную папку, пользователь видел
            # названия ВСЕХ её подпапок, включая закрытые для него —
            # открыть их он не мог (PermissionDenied), но само название
            # уже раскрывалось.
            context['subfolders'] = DepartmentFolder.objects.filter(
                parent=self.current_folder, allowed_users=self.request.user,
            ).distinct()
        else:
            context['subfolders'] = _visible_root_folders(self.request.user)

        context['trash_url'] = reverse('storage_trash')
        # Модалка создания папки исключает текущего пользователя из списка —
        # он и так получает доступ автоматически (см. CreateDepartmentFolderView),
        # текст рядом со списком прямо говорит "кроме вас". Модалка же
        # редактирования доступа существующей папки должна показывать ПОЛНЫЙ
        # список, включая создателя: он реально состоит в allowed_users, и
        # без своей строки в чекбоксах при сохранении формы терял бы себе
        # доступ — его pk просто не мог попасть в отправленный список.
        context['all_users_except_me'] = get_user_model().objects.exclude(
            pk=self.request.user.pk
        ).order_by('last_name', 'first_name')
        context['all_users'] = get_user_model().objects.order_by('last_name', 'first_name')
        # Папки, доступные пользователю — для селекта перемещения, с
        # отступами по вложенности.
        context['accessible_folders'] = build_folder_choices(
            DepartmentFolder.objects.filter(allowed_users=self.request.user).distinct()
        )
        context.update(self.get_fm_context(self.active_sort, self.active_filters))
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

        _notify_folder(parent_id, actor=request.user,
                       action='folder_created', text=f'создал папку «{name}»')
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

        _notify_folder(folder.pk, actor=request.user,
                       action='access_changed', text='изменил список доступа к папке')
        return JsonResponse({'success': True, 'lost_access': str(request.user.pk) not in user_ids})


class RenameDepartmentFolderView(LoginRequiredMixin, View):
    def post(self, request, folder_id):
        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        folder.name = name
        folder.save(update_fields=['name'])

        _notify_folder(folder.parent_id, actor=request.user,
                       action='folder_renamed', text=f'переименовал папку в «{name}»')
        return JsonResponse({'success': True, 'name': folder.name})


class DeleteDepartmentFolderView(LoginRequiredMixin, View):
    """Удаление папки вместе с содержимым.

    В отличие от обменника и каталога, содержимое нельзя просто отвязать:
    права `DepartmentDocument` наследуются от папки, и `folder=None` значит
    «не виден никому», включая корзину. Поэтому документы поддерева
    переезжают в родительскую папку и помечаются удалёнными — её права у
    удаляющего заведомо есть.

    У корневой папки родителя нет, девать документы некуда, и удаление
    отклоняется с объяснением.
    """

    def post(self, request, folder_id):
        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        parent = folder.parent
        name = folder.name
        subtree_ids = folder_subtree_ids(folder)
        documents = DepartmentDocument.objects.filter(folder_id__in=subtree_ids)

        if parent is None and documents.exists():
            return JsonResponse({
                'success': False,
                'error': 'В папке есть документы, а перенести их некуда: это папка верхнего '
                         'уровня. Сначала перенесите документы в другую папку или удалите их.',
            }, status=400)

        with transaction.atomic():
            if parent is not None:
                documents.filter(is_deleted=False).update(
                    folder=parent, is_deleted=True,
                    deleted_at=timezone.now(), deleted_by=request.user,
                )
                # Уже лежавшие в корзине — тоже к родителю, иначе каскад
                # снёс бы их безвозвратно вместе с папкой.
                documents.filter(is_deleted=True).update(folder=parent)
            folder.delete()

        _notify_folder(parent.pk if parent else None, actor=request.user,
                       action='folder_deleted', text=f'удалил папку «{name}»')
        return JsonResponse({'success': True})


class MoveDepartmentFolderView(LoginRequiredMixin, View):
    """Перенос требует доступа и к переносимой папке, и к новому
    родителю — иначе можно было бы утащить чужую (недоступную) папку
    внутрь своей, или свою — внутрь чужой, к которой нет доступа."""

    def post(self, request, folder_id):
        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        new_parent_id = request.POST.get('parent_id') or None
        old_parent_id = folder.parent_id

        if new_parent_id:
            new_parent = get_object_or_404(DepartmentFolder, pk=new_parent_id)
            if not new_parent.is_accessible_by(request.user):
                raise PermissionDenied
            if new_parent.pk == folder.pk or _is_descendant(new_parent, folder):
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


def _is_descendant(candidate: DepartmentFolder, ancestor: DepartmentFolder) -> bool:
    """seen страхует от зацикливания на уже существующем цикле в данных —
    без него обход вверх по parent не закончился бы никогда."""
    node = candidate
    seen = set()
    while node.parent_id is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.parent_id == ancestor.pk:
            return True
        node = node.parent
    return False


class RenameDepartmentDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        title = request.POST.get('title', '').strip()
        if not title:
            return JsonResponse({'success': False, 'error': 'Укажите название документа'}, status=400)

        document.title = title
        document.save(update_fields=['title'])

        _notify_folder(document.folder_id, actor=request.user,
                       action='file_renamed', text=f'переименовал документ в «{title}»')
        return JsonResponse({'success': True, 'title': document.title})


class MoveDepartmentDocumentView(LoginRequiredMixin, View):
    """Документ вне папки не наследует ничьих прав (DepartmentDocument
    docstring), поэтому перенос в None (корень) здесь не предлагается —
    только между папками, к обеим из которых есть доступ."""

    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        folder_id = request.POST.get('folder_id')
        if not folder_id:
            return JsonResponse({'success': False, 'error': 'Выберите папку назначения'}, status=400)

        target_folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not target_folder.is_accessible_by(request.user):
            raise PermissionDenied

        source_folder_id = document.folder_id
        document.folder = target_folder
        document.save(update_fields=['folder'])

        _notify_folder(source_folder_id, actor=request.user,
                       action='file_moved', text='переместил документ')
        if source_folder_id != target_folder.pk:
            _notify_folder(target_folder.pk, actor=request.user,
                           action='file_moved', text='переместил сюда документ')
        return JsonResponse({'success': True})


class BulkTrashDepartmentDocumentsView(LoginRequiredMixin, View):
    """Массовое удаление в корзину — только среди документов, доступных
    текущему пользователю; чужие id в списке молча игнорируются queryset'ом,
    а не вызывают PermissionDenied, чтобы не раскрывать, какие id вообще
    существуют. Уходит в Celery (storage.tasks.bulk_trash_documents) — тот
    же принцип, что и в catalog.BulkTrashCatalogDocumentsView."""

    def post(self, request):
        doc_ids = request.POST.getlist('doc_ids')
        if not doc_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)

        accessible_folder_ids = list(
            DepartmentFolder.objects.filter(allowed_users=request.user).values_list('pk', flat=True)
        )

        from storage.tasks import bulk_trash_documents

        task = bulk_trash_documents.delay(
            'deptdocs', 'DepartmentDocument', doc_ids, request.user.pk,
            extra_filter={'folder_id__in': accessible_folder_ids},
        )

        affected = set(
            DepartmentDocument.objects.filter(
                pk__in=doc_ids, folder_id__in=accessible_folder_ids,
            ).values_list('folder_id', flat=True)
        )
        for folder_id in affected:
            _notify_folder(folder_id, actor=request.user,
                           action='files_trashed', text='удалил документы в корзину')

        return fm_task_response(request, task)


class DownloadDepartmentFolderView(LoginRequiredMixin, View):
    """Скачать папку целиком одним архивом.

    Единственный из трёх модулей, где обход поддерева обязан считаться с
    правами на КАЖДУЮ папку: allowed_users задаётся на папке, поэтому
    вложенная может быть закрыта при открытой родительской. Ветка с
    закрытой папкой обрывается целиком — иначе архив стал бы способом
    выгрузить то, что в интерфейсе даже не показывается.
    """

    def get(self, request, folder_id):
        folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        paths = folder_subtree(
            folder,
            queryset=DepartmentFolder.objects.filter(allowed_users=request.user).distinct(),
        )

        documents = DepartmentDocument.objects.filter(
            folder_id__in=paths, is_deleted=False,
        ).select_related('file_object__blob')

        return fm_folder_archive_view(
            request,
            items=[(paths[document.folder_id], document.file_object) for document in documents],
            filename=f'{folder.name}.zip',
        )


class UploadDepartmentArchiveView(LoginRequiredMixin, View):
    """Загрузить zip и распаковать его в папку приватного доступа.

    folder_id обязателен, в отличие от каталога: документ вне папки не
    наследует ничьих прав и не виден никому (DepartmentDocument), то есть
    распаковка «в корень» молча спрятала бы всё загруженное.
    """

    def post(self, request):
        folder = get_object_or_404(DepartmentFolder, pk=request.POST.get('folder_id') or 0)
        if not folder.is_accessible_by(request.user):
            raise PermissionDenied

        from .tasks import import_deptdocs_archive

        return fm_archive_upload_view(
            request,
            category=FileObject.Category.DOCUMENT,
            launch=lambda archive: import_deptdocs_archive.delay(
                archive.pk, folder.pk, request.user.pk,
            ),
        )


class BulkDownloadDepartmentDocumentsView(LoginRequiredMixin, View):
    """Скачать выбранные документы одним архивом.

    Единственный из трёх модулей, где фильтр не сводится к is_deleted:
    документ не хранит прав, а наследует их от folder.allowed_users, и
    массовое скачивание обязано сузиться по тому же признаку, что и
    одиночное (DownloadDepartmentDocumentView: is_accessible_by). Иначе
    достаточно было бы перечислить id в query-строке, чтобы выгрузить
    закрытые папки архивом.
    """

    def get(self, request):
        return fm_archive_view(
            request,
            DepartmentDocument.objects.filter(
                folder__allowed_users=request.user, is_deleted=False,
            ).distinct(),
            filename='Приватный доступ.zip',
        )


class BulkMoveDepartmentDocumentsView(LoginRequiredMixin, View):
    """Массовое перемещение между доступными папками.

    Как и в BulkTrash, право проверяется ЗДЕСЬ и в задачу уходит уже
    ограниченный список: сама задача прав не проверяет (ARCHITECTURE.md,
    чек-лист потребителя). Папка назначения обязана быть доступной —
    иначе массовым переносом можно было бы положить документы туда, куда
    одиночный перенос их не пускает."""

    def post(self, request):
        doc_ids = request.POST.getlist('doc_ids')
        folder_id = request.POST.get('folder_id')

        if not doc_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)
        if not folder_id:
            return JsonResponse({'success': False, 'error': 'Выберите папку назначения'}, status=400)

        target_folder = get_object_or_404(DepartmentFolder, pk=folder_id)
        if not target_folder.is_accessible_by(request.user):
            raise PermissionDenied

        accessible_folder_ids = list(
            DepartmentFolder.objects.filter(allowed_users=request.user).values_list('pk', flat=True)
        )
        source_folder_ids = set(
            DepartmentDocument.objects.filter(
                pk__in=doc_ids, folder_id__in=accessible_folder_ids,
            ).values_list('folder_id', flat=True)
        )

        from storage.tasks import bulk_move_documents

        task = bulk_move_documents.delay(
            'deptdocs', 'DepartmentDocument', doc_ids, 'folder_id', target_folder.pk,
            extra_filter={'folder_id__in': accessible_folder_ids},
        )

        for source_id in source_folder_ids | {target_folder.pk}:
            _notify_folder(source_id, actor=request.user,
                           action='files_moved', text='переместил документы')
        return fm_task_response(request, task)


class SendDepartmentDocumentToExchangeView(LoginRequiredMixin, View):
    """Переслать документ отдела в обменник — новый FileObject на тот же
    blob (StorageService.copy_reference). Копия уходит в обменник со своими
    открытыми правами: пересылка — осознанное действие того, у кого уже
    есть доступ к оригиналу, дальше это его выбор, кому показать."""

    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

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
                f'«{document.title}»',
                actor=request.user, url=reverse('exchange_folder', args=[recipient.pk]),
            )

        realtime.broadcast(
            realtime.SCOPE_EXCHANGE, realtime.exchange_location(recipient.pk, None),
            action='file_created', actor=request.user, text='переслал сюда документ',
        )
        return JsonResponse({'success': True})


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

        # Уведомляем всех участников папки, кроме самого загрузившего —
        # у папки нет единого "владельца", доступ равноправный, поэтому
        # получателей несколько, в отличие от обменника с одним владельцем.
        recipients = folder.allowed_users.exclude(pk=request.user.pk)
        for recipient in recipients:
            notify(
                recipient, Notification.Kind.FILE_SHARED,
                f'{request.user.get_full_name() or request.user.username} добавил документ '
                f'в папку «{folder.name}»',
                actor=request.user, url=reverse('deptdocs_folder', args=[folder.pk]),
            )

        _notify_folder(folder.pk, actor=request.user,
                       action='file_created', text='добавил документ')
        return JsonResponse({'success': True})


class DownloadDepartmentDocumentView(LoginRequiredMixin, View):
    """?inline=1 — открыть в браузере вместо скачивания."""

    def get(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)

        # Права — здесь, не в storage (ARCHITECTURE.md, раздел 8).
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        inline = request.GET.get('inline') == '1'
        return StorageService.get_download_response(document.file_object, request, inline=inline)


class TrashDepartmentDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        document = get_object_or_404(DepartmentDocument, pk=doc_id, is_deleted=False)
        if not document.is_accessible_by(request.user):
            raise PermissionDenied

        document.is_deleted = True
        document.deleted_at = timezone.now()
        document.deleted_by = request.user
        document.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])

        _notify_folder(document.folder_id, actor=request.user,
                       action='file_trashed', text='удалил документ')
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

        _notify_folder(document.folder_id, actor=request.user,
                       action='file_restored', text='восстановил документ')
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
        context['trash_url'] = reverse('storage_trash')
        return context
