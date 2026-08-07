from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
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
    fm_archive_upload_view,
    fm_archive_view,
    fm_task_response,
    folder_ancestors,
)

from profiles.models import Notification
from profiles.views._common import notify

from .models import ExchangeFile, ExchangeFolder


def _notify_folder(folder, owner_id, *, actor, action, text):
    """Сообщает всем, кто сейчас смотрит эту папку обменника, что её
    содержимое изменилось (storage/realtime.py). Вызывается из каждой
    мутирующей вьюхи: без этого сосед узнавал бы о новом файле только
    обновив страницу вручную."""
    realtime.broadcast(
        realtime.SCOPE_EXCHANGE,
        realtime.exchange_location(owner_id, folder.pk if folder else None),
        action=action, actor=actor, text=text,
    )


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
        context['trash_url'] = reverse('storage_trash')
        # Панель поиска общая для всех страниц менеджера и читает значение
        # из active_filters — здесь ищут сотрудника, а не файл, поэтому
        # расширенные поля («размер», «кто загрузил») к списку папок
        # неприменимы и прячутся параметром fm_simple_search в шаблоне.
        query = self.request.GET.get('q', '').strip()
        context['active_filters'] = {'q': query} if query else {}
        context['exchange_ttl_days'] = StorageService.get_category_ttl_days(
            FileObject.Category.EXCHANGE
        )
        return context


class ExchangeFolderView(PartialGridMixin, LoginRequiredMixin, ListView):
    """Содержимое папки сотрудника, включая навигацию по подпапкам.
    folder_id=None — корень личной папки владельца. Видно всем — как в
    сетевой папке; создавать подпапки может любой сотрудник, удалять —
    владелец личной папки или тот, кто создал подпапку
    (ExchangeFolder.can_be_deleted_by)."""

    template_name = 'exchange/folder.html'
    partial_template_name = 'exchange/_grid.html'
    context_object_name = 'files'

    def get_queryset(self):
        self.folder_owner = get_object_or_404(get_user_model(), pk=self.kwargs['user_id'])
        folder_id = self.kwargs.get('folder_id')

        if folder_id:
            # Подпапка обязана принадлежать owner'у из URL — иначе можно
            # было бы подставить чужой folder_id и увидеть содержимое не
            # той личной папки, в которую ведёт хлебная крошка.
            self.current_folder = get_object_or_404(
                ExchangeFolder, pk=folder_id, owner=self.folder_owner
            )
        else:
            self.current_folder = None

        files = ExchangeFile.objects.filter(
            owner=self.folder_owner, folder=self.current_folder, is_deleted=False
        ).select_related('file_object', 'file_object__blob', 'uploaded_by')

        files, self.active_filters = apply_filters(
            files, self.request.GET, name_field='file_object__original_name',
        )
        files, self.active_sort = apply_sort(
            files, self.request.GET.get('sort', ''), name_field='file_object__original_name',
        )
        return files

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['folder_owner'] = self.folder_owner
        context['is_own_folder'] = self.folder_owner.pk == self.request.user.pk
        context['current_folder'] = self.current_folder
        context['folder_path'] = folder_ancestors(self.current_folder)
        context['subfolders'] = ExchangeFolder.objects.filter(
            owner=self.folder_owner, parent=self.current_folder
        )
        context['trash_url'] = reverse('storage_trash')
        # Плоский список ВСЕХ подпапок этой личной папки (не только на
        # текущем уровне) — для селекта перемещения файла: переносят обычно
        # в известную подпапку, искать её в плоском списке проще, чем
        # разворачивать многоуровневое дерево ради разового выбора.
        context['all_owner_subfolders'] = ExchangeFolder.objects.filter(owner=self.folder_owner)
        # Срок хранения — из политики storage, а не числом в шаблоне:
        # при смене STORAGE_CATEGORY_TTL текст в модалке загрузки иначе
        # продолжил бы обещать старый срок.
        context['exchange_ttl_days'] = StorageService.get_category_ttl_days(
            FileObject.Category.EXCHANGE
        )
        context.update(self.get_fm_context(self.active_sort, self.active_filters))
        return context


class CreateExchangeFolderView(LoginRequiredMixin, View):
    """Подпапку в любой личной папке может завести любой сотрудник — как и
    положить туда файл (обменник открыт на запись всем, ARCHITECTURE.md,
    раздел 1.1)."""

    def post(self, request, user_id):
        folder_owner = get_object_or_404(get_user_model(), pk=user_id)

        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent_id') or None

        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        if parent_id:
            parent = get_object_or_404(ExchangeFolder, pk=parent_id, owner=folder_owner)
        else:
            parent = None

        folder = ExchangeFolder.objects.create(
            name=name, owner=folder_owner, parent=parent, created_by=request.user,
        )
        _notify_folder(
            parent, folder_owner.pk, actor=request.user,
            action='folder_created', text=f'создал папку «{name}»',
        )
        return JsonResponse({'success': True, 'id': folder.pk, 'name': folder.name})


class DeleteExchangeFolderView(LoginRequiredMixin, View):
    """Удаляет подпапку. Файлы внутри — включая вложенные подпапки —
    сначала переезжают в корзину, и только потом удаляется сама папка.

    Раньше папка удалялась сразу, а файлы уносил каскад
    (ExchangeFile.folder = CASCADE): blob'ы освобождались корректно, но для
    пользователя это был единственный способ безвозвратно потерять файлы
    одним кликом — во всём остальном менеджере удаление обратимо через
    корзину (ARCHITECTURE.md, раздел 6). Теперь удаление папки ничем не
    отличается от удаления её содержимого по одному.

    Удалять может владелец личной папки или тот, кто создал подпапку — тот
    же принцип, что и для отдельных файлов."""

    def post(self, request, folder_id):
        folder = get_object_or_404(ExchangeFolder, pk=folder_id)
        if not folder.can_be_deleted_by(request.user):
            raise PermissionDenied

        parent = folder.parent
        owner_id = folder.owner_id
        name = folder.name

        with transaction.atomic():
            subtree_ids = self._subtree_ids(folder)
            # folder=None обязательно, а не только is_deleted=True:
            # ExchangeFile.folder — CASCADE, и без отвязки от папки
            # folder.delete() всё равно снёс бы записи физически, сколько
            # бы флагов на них ни стояло. У обменника folder=None —
            # штатное состояние «файл в корне личной папки», так что
            # восстановление из корзины вернёт файл именно туда.
            ExchangeFile.objects.filter(folder_id__in=subtree_ids, is_deleted=False).update(
                folder=None, is_deleted=True, deleted_at=timezone.now(), deleted_by=request.user,
            )
            # Уже лежавшие в корзине файлы этой папки тоже нужно отвязать,
            # иначе каскад удалит их безвозвратно вместе с папкой.
            ExchangeFile.objects.filter(folder_id__in=subtree_ids, is_deleted=True).update(folder=None)
            folder.delete()

        _notify_folder(
            parent, owner_id, actor=request.user,
            action='folder_deleted', text=f'удалил папку «{name}»',
        )
        return JsonResponse({'success': True})

    @staticmethod
    def _subtree_ids(folder) -> list:
        """Id папки и всех вложенных. Обход в ширину по уровням: дерево
        подпапок неглубокое, и несколько запросов по parent_id дешевле и
        понятнее рекурсивного CTE."""
        ids = [folder.pk]
        level = [folder.pk]
        while level:
            level = list(
                ExchangeFolder.objects.filter(parent_id__in=level).values_list('pk', flat=True)
            )
            ids.extend(level)
        return ids


class UploadExchangeFileView(LoginRequiredMixin, View):
    """Загрузка в папку любого сотрудника — как положить файл в чужую папку
    на сетевом диске. folder_id (опционально) — подпапка внутри личной
    папки владельца; None означает файл прямо в корне."""

    def post(self, request, user_id):
        folder_owner = get_object_or_404(get_user_model(), pk=user_id)
        uploaded_files = request.FILES.getlist('files') or (
            [request.FILES['file']] if 'file' in request.FILES else []
        )

        if not uploaded_files:
            return JsonResponse({'success': False, 'error': 'Файл не выбран'}, status=400)

        folder_id = request.POST.get('folder_id') or None
        folder = (
            get_object_or_404(ExchangeFolder, pk=folder_id, owner=folder_owner)
            if folder_id else None
        )

        created = 0
        failed = []

        for uploaded in uploaded_files:
            try:
                file_object = StorageService.upload(
                    uploaded, user=request.user, category=FileObject.Category.EXCHANGE,
                )
            except FileTooLargeError:
                failed.append(f'«{uploaded.name}» — слишком большой')
                continue
            except QuotaExceededError:
                failed.append(f'«{uploaded.name}» — превышена квота хранилища')
                continue

            ExchangeFile.objects.create(
                file_object=file_object, owner=folder_owner, folder=folder, uploaded_by=request.user,
            )
            created += 1

        # Раньше первая же ошибка возвращала 400 и обрывала цикл: уже
        # загруженные файлы оставались в папке, а пользователь видел
        # «не удалось» и не знал, что половина всё-таки загрузилась.
        # Теперь отчёт соответствует тому, что реально произошло.
        if created:
            if folder_owner.pk != request.user.pk:
                notify(
                    folder_owner, Notification.Kind.FILE_SHARED,
                    f'{request.user.get_full_name() or request.user.username} добавил файл в вашу папку обменника'
                    + (f' ({created} шт.)' if created > 1 else ''),
                    actor=request.user,
                    url=reverse('exchange_folder', args=[folder_owner.pk]),
                )

            _notify_folder(
                folder, folder_owner.pk, actor=request.user,
                action='files_uploaded',
                text=f'загрузил файлов: {created}' if created > 1 else 'загрузил файл',
            )

        if failed and not created:
            return JsonResponse({'success': False, 'error': '; '.join(failed)}, status=400)

        return JsonResponse({
            'success': True,
            'created': created,
            'skipped': failed,
            'error': '; '.join(failed) if failed else '',
        })


class RenameExchangeFolderView(LoginRequiredMixin, View):
    def post(self, request, folder_id):
        folder = get_object_or_404(ExchangeFolder, pk=folder_id)
        if not folder.can_be_deleted_by(request.user):
            raise PermissionDenied

        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название папки'}, status=400)

        folder.name = name
        folder.save(update_fields=['name'])

        _notify_folder(
            folder.parent, folder.owner_id, actor=request.user,
            action='folder_renamed', text=f'переименовал папку в «{name}»',
        )
        return JsonResponse({'success': True, 'name': folder.name})


class MoveExchangeFolderView(LoginRequiredMixin, View):
    """Перенос подпапки внутри одной и той же личной папки.

    У каталога и приватного доступа перенос папок был с самого начала, у
    обменника — нет, из-за чего собранную не в той подпапке структуру
    приходилось пересоздавать руками.

    Владелец не меняется по той же причине, что и у файлов
    (MoveExchangeFileView): перенос содержимого в чужую личную папку
    выглядел бы для неё как подмена без предупреждения."""

    def post(self, request, folder_id):
        folder = get_object_or_404(ExchangeFolder, pk=folder_id)
        if not folder.can_be_deleted_by(request.user):
            raise PermissionDenied

        old_parent = folder.parent
        new_parent_id = request.POST.get('parent_id') or None

        if new_parent_id:
            new_parent = get_object_or_404(
                ExchangeFolder, pk=new_parent_id, owner_id=folder.owner_id,
            )
            if new_parent.pk == folder.pk or _is_descendant(new_parent, folder):
                return JsonResponse(
                    {'success': False,
                     'error': 'Нельзя перенести папку в саму себя или во вложенную папку'},
                    status=400,
                )
        else:
            new_parent = None

        folder.parent = new_parent
        folder.save(update_fields=['parent'])

        _notify_folder(old_parent, folder.owner_id, actor=request.user,
                       action='folder_moved', text=f'перенёс папку «{folder.name}»')
        if (old_parent.pk if old_parent else None) != (new_parent.pk if new_parent else None):
            _notify_folder(new_parent, folder.owner_id, actor=request.user,
                           action='folder_moved', text=f'перенёс сюда папку «{folder.name}»')

        return JsonResponse({'success': True})


def _is_descendant(candidate: ExchangeFolder, ancestor: ExchangeFolder) -> bool:
    """True, если candidate лежит внутри поддерева ancestor. Без этой
    проверки папку можно было бы перенести внутрь самой себя, и дерево
    self-FK замкнулось бы в цикл, а обход подпапок — завис.

    seen страхует от уже существующего в данных цикла."""
    node = candidate
    seen = set()
    while node.parent_id is not None and node.pk not in seen:
        seen.add(node.pk)
        if node.parent_id == ancestor.pk:
            return True
        node = node.parent
    return False


class RenameExchangeFileView(LoginRequiredMixin, View):
    """Переименовывает FileObject.original_name — у ExchangeFile своего
    заголовка нет, имя всегда берётся из именованного объекта storage."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)
        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Укажите название файла'}, status=400)

        exchange_file.file_object.original_name = name
        exchange_file.file_object.save(update_fields=['original_name'])

        _notify_folder(
            exchange_file.folder, exchange_file.owner_id, actor=request.user,
            action='file_renamed', text=f'переименовал файл в «{name}»',
        )
        return JsonResponse({'success': True, 'name': name})


class MoveExchangeFileView(LoginRequiredMixin, View):
    """Перемещение между подпапками внутри ОДНОЙ И ТОЙ ЖЕ личной папки —
    менять owner здесь не предполагается: перекладывание файла из папки
    одного сотрудника в папку другого выглядело бы для пострадавшего как
    подмена содержимого его личной папки без предупреждения. Для передачи
    в другую личную папку — заново положить файл (upload) или переслать
    через copy_reference из другого модуля."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)
        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        folder_id = request.POST.get('folder_id') or None
        folder = (
            get_object_or_404(ExchangeFolder, pk=folder_id, owner=exchange_file.owner)
            if folder_id else None
        )

        source_folder = exchange_file.folder
        exchange_file.folder = folder
        exchange_file.save(update_fields=['folder'])

        # Обновиться должны обе папки: и та, откуда файл ушёл, и та, куда
        # приехал — иначе у смотрящего на папку-источник он так и висел бы.
        _notify_folder(source_folder, exchange_file.owner_id, actor=request.user,
                       action='file_moved', text='переместил файл')
        if (source_folder.pk if source_folder else None) != (folder.pk if folder else None):
            _notify_folder(folder, exchange_file.owner_id, actor=request.user,
                           action='file_moved', text='переместил сюда файл')

        return JsonResponse({'success': True})


class BulkTrashExchangeFilesView(LoginRequiredMixin, View):
    """Массовое удаление в корзину — только среди файлов, которые текущий
    пользователь имеет право удалить (владелец папки или загрузивший).

    Право на удаление здесь — Q(owner=X) | Q(uploaded_by=X), это не сводится
    к простому dict-фильтру, который storage.tasks.bulk_trash_documents ждёт
    в extra_filter, поэтому список id сужается до разрешённых ЗДЕСЬ, одним
    запросом, а в задачу уходит уже урезанный список — сама задача просто
    доверяет присланным id."""

    def post(self, request):
        file_ids = request.POST.getlist('file_ids')
        if not file_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)

        allowed = list(
            ExchangeFile.objects.filter(
                Q(owner=request.user) | Q(uploaded_by=request.user),
                pk__in=file_ids, is_deleted=False,
            ).values_list('pk', 'owner_id', 'folder_id')
        )

        if not allowed:
            # Именно 200, а не 403: отвечать «нет прав» на конкретные id
            # значит подтверждать, что такие id существуют. Но и молчать
            # нельзя — раньше пользователь просто не видел никакой реакции,
            # поэтому ответ несёт message, который фронт покажет тостом.
            return JsonResponse({
                'success': True, 'task_id': None, 'done': 0,
                'message': 'Ничего не удалено: нет прав на выбранные файлы',
            })

        from storage.tasks import bulk_trash_documents

        task = bulk_trash_documents.delay(
            'exchange', 'ExchangeFile', [pk for pk, _, _ in allowed], request.user.pk,
        )

        for owner_id, folder_id in {(owner_id, folder_id) for _, owner_id, folder_id in allowed}:
            realtime.broadcast(
                realtime.SCOPE_EXCHANGE, realtime.exchange_location(owner_id, folder_id),
                action='files_trashed', actor=request.user, text='удалил файлы в корзину',
            )

        return fm_task_response(request, task)


class UploadExchangeArchiveView(LoginRequiredMixin, View):
    """Загрузить zip и распаковать его в личную папку сотрудника.

    Права те же, что и у обычной загрузки (UploadExchangeFileView): положить
    архив в чужую личную папку можно так же, как отдельный файл — обменник
    открыт на запись всем.
    """

    def post(self, request, user_id):
        folder_owner = get_object_or_404(get_user_model(), pk=user_id)

        folder_id = request.POST.get('folder_id') or None
        if folder_id:
            get_object_or_404(ExchangeFolder, pk=folder_id, owner=folder_owner)

        from .tasks import import_exchange_archive

        return fm_archive_upload_view(
            request,
            category=FileObject.Category.EXCHANGE,
            launch=lambda archive: import_exchange_archive.delay(
                archive.pk, folder_owner.pk, folder_id, request.user.pk,
            ),
        )


class BulkDownloadExchangeFilesView(LoginRequiredMixin, View):
    """Скачать выбранные файлы одним архивом.

    Права те же, что и у одиночного скачивания (DownloadExchangeFileView):
    содержимое обменника видно всем сотрудникам, ограничение только на
    удаление и перенос. Поэтому фильтр здесь — лишь is_deleted=False;
    выбирать файлы из корзины пачкой нельзя ровно так же, как и по одному.
    """

    def get(self, request):
        return fm_archive_view(
            request,
            ExchangeFile.objects.filter(is_deleted=False),
            filename='Обменник.zip',
        )


class BulkMoveExchangeFilesView(LoginRequiredMixin, View):
    """Массовое перемещение между подпапками одной личной папки.

    Раньше массового переноса у обменника не было вовсе — в панели
    выделения оставалось только удаление, хотя выделять файлы пачкой уже
    было можно. Целевая подпапка обязана принадлежать тому же владельцу,
    что и переносимые файлы: иначе через bulk можно было бы сделать то,
    что запрещено в MoveExchangeFileView — переложить файл в чужую личную
    папку."""

    def post(self, request):
        file_ids = request.POST.getlist('file_ids')
        if not file_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)

        folder_id = request.POST.get('folder_id') or None
        folder = get_object_or_404(ExchangeFolder, pk=folder_id) if folder_id else None

        allowed_qs = ExchangeFile.objects.filter(
            Q(owner=request.user) | Q(uploaded_by=request.user),
            pk__in=file_ids, is_deleted=False,
        )
        if folder is not None:
            allowed_qs = allowed_qs.filter(owner_id=folder.owner_id)

        allowed_ids = list(allowed_qs.values_list('pk', flat=True))
        if not allowed_ids:
            # 200 с сообщением, а не 403 — см. BulkTrashExchangeFilesView.
            return JsonResponse({
                'success': True, 'task_id': None, 'done': 0,
                'message': 'Ничего не перемещено: нет прав на выбранные файлы '
                           'или папка назначения принадлежит другому сотруднику',
            })

        from storage.tasks import bulk_move_documents

        task = bulk_move_documents.delay(
            'exchange', 'ExchangeFile', allowed_ids, 'folder_id', folder.pk if folder else None,
        )

        _notify_folder(folder, folder.owner_id if folder else request.user.pk,
                       actor=request.user, action='files_moved', text='переместил файлы')
        return fm_task_response(request, task)


class SendExchangeFileToCatalogView(LoginRequiredMixin, View):
    """Переслать файл из обменника в общий информационный каталог — новый
    FileObject на тот же blob (copy_reference). Права каталога тривиальны
    (LoginRequiredMixin), поэтому переслать может любой, кто видит файл в
    обменнике — то есть кто угодно, обменник открыт всем."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)

        try:
            copied_object = StorageService.copy_reference(
                exchange_file.file_object, user=request.user, category=FileObject.Category.CATALOG,
            )
        except QuotaExceededError:
            return JsonResponse({'success': False, 'error': 'Превышена квота хранилища'}, status=400)

        from catalog.models import CatalogDocument

        CatalogDocument.objects.create(
            file_object=copied_object, title=copied_object.original_name, uploaded_by=request.user,
        )

        realtime.broadcast(
            realtime.SCOPE_CATALOG, realtime.folder_location(None),
            action='file_created', actor=request.user, text='добавил документ в каталог',
        )
        return JsonResponse({'success': True})


class DownloadExchangeFileView(LoginRequiredMixin, View):
    """Скачивание доступно всем сотрудникам: содержимое папок обменника
    открыто, как в сетевой папке. Ограничение только на удаление.

    ?inline=1 — открыть в браузере вместо скачивания."""

    def get(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)
        inline = request.GET.get('inline') == '1'
        return StorageService.get_download_response(exchange_file.file_object, request, inline=inline)


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

        _notify_folder(
            exchange_file.folder, exchange_file.owner_id, actor=request.user,
            action='file_trashed', text='удалил файл',
        )
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
        context['trash_url'] = reverse('storage_trash')
        context['exchange_ttl_days'] = StorageService.get_category_ttl_days(
            FileObject.Category.EXCHANGE
        )
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

        _notify_folder(
            exchange_file.folder, exchange_file.owner_id, actor=request.user,
            action='file_restored', text='восстановил файл',
        )
        return JsonResponse({'success': True})


class PurgeExchangeFileView(LoginRequiredMixin, View):
    """Окончательное удаление — только из корзины."""

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=True)

        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        # detach() вызывать не нужно — его выполнит сигнал post_delete;
        # пометка лишь сохраняет в журнале, кто инициировал удаление.
        attribute_deletion(exchange_file, user=request.user, consumer='exchange.ExchangeFile')
        exchange_file.delete()
        return JsonResponse({'success': True})
