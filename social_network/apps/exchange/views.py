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


def _notify_new_owner(new_owner, *, actor, text):
    """Владельцу личной папки — что в неё что-то приехало не его руками.

    Тот же Kind.FILE_SHARED и та же ссылка, что и у загрузки
    (UploadExchangeFileView): для владельца папки разницы между «положили
    файл» и «перенесли файл» нет — содержимое его папки изменилось без его
    участия, и узнать об этом он должен так же.
    """
    if new_owner.pk == actor.pk:
        return

    notify(
        new_owner, Notification.Kind.FILE_SHARED,
        f'{actor.get_full_name() or actor.username} {text}',
        actor=actor,
        url=reverse('exchange_folder', args=[new_owner.pk]),
    )


def _move_destination(request, *, default_owner_id, folder_param='folder_id'):
    """Разбирает «куда переносим» → (owner, folder).

    owner=None — владельца не менять, folder=None — корень личной папки.

    Владельца задаёт сама подпапка: она знает, в чьей личной папке заведена.
    `owner_id` нужен только для корня и, присланный вместе с подпапкой,
    обязан с ней совпасть: пара «папка одного + подпапка другого» дала бы
    файл, невидимый нигде — список фильтруется по owner И folder сразу.
    """
    owner_id = request.POST.get('owner_id') or None
    folder_id = request.POST.get(folder_param) or None

    if folder_id:
        folders = ExchangeFolder.objects.select_related('owner')
        folder = (
            get_object_or_404(folders, pk=folder_id, owner_id=owner_id) if owner_id
            else get_object_or_404(folders, pk=folder_id)
        )
        return folder.owner, folder

    owner_id = owner_id or default_owner_id
    return (get_object_or_404(get_user_model(), pk=owner_id) if owner_id else None), None


def _move_targets(current_owner):
    """Дерево «сотрудник → его подпапки» для селекта перемещения.

    Раньше в селекте были только подпапки текущей личной папки: перенос
    между сотрудниками был запрещён, и предлагать чужие папки было незачем.
    Теперь перенос разрешён (MoveExchangeFileView), а контрол обязан
    предлагать ровно то, что действительно сработает (ARCHITECTURE.md 12.4).

    Корневая «папка» обменника — это сам сотрудник, а не запись
    ExchangeFolder, поэтому список строится от пользователей, а не одним
    build_folder_choices по всем папкам: отступы внутри личной папки считает
    он же, но каждой личной папке — отдельно.

    Два запроса на весь список независимо от числа сотрудников: папки
    группируются по owner_id в Python.
    """
    folders_by_owner = {}
    for folder in ExchangeFolder.objects.all():
        folders_by_owner.setdefault(folder.owner_id, []).append(folder)

    return [
        {
            'owner_id': user.pk,
            'title': user.get_full_name() or user.username,
            'is_current': user.pk == current_owner.pk,
            'folders': build_folder_choices(folders_by_owner.get(user.pk, [])),
        }
        for user in get_user_model().objects.order_by('last_name', 'first_name')
    ]


class ExchangeFileMixin:
    """Что базовые вьюхи storage должны знать про файл обменника.

    Скачивание доступно всем сотрудникам — содержимое папок открыто, как в
    сетевой папке; ограничение только на изменение и удаление.
    """

    model = ExchangeFile
    pk_kwarg = 'file_id'
    noun = 'файл'

    def check_permission(self, request, obj):
        if not obj.can_be_deleted_by(request.user):
            raise PermissionDenied

    def notify(self, obj, *, actor, action, text):
        _notify_folder(obj.folder, obj.owner_id, actor=actor, action=action, text=text)


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
        # Куда можно перенести файл или подпапку: личные папки всех
        # сотрудников со своими подпапками. Плоский список подпапок только
        # текущей личной папки, стоявший здесь раньше, соответствовал
        # прежнему запрету на перенос между сотрудниками.
        context['move_targets'] = _move_targets(self.folder_owner)
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
            subtree_ids = folder_subtree_ids(folder)
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
    """Перенос подпапки — в том числе в личную папку другого сотрудника.

    У каталога и приватного доступа перенос папок был с самого начала, у
    обменника — нет, из-за чего собранную не в той подпапке структуру
    приходилось пересоздавать руками.

    Смена владельца — по той же причине и с теми же последствиями, что и у
    файлов (MoveExchangeFileView). Владелец меняется у ВСЕГО поддерева,
    иначе перенос сломал бы видимость: список файлов фильтруется по owner и
    folder сразу, и файл со старым владельцем в переехавшей папке пропал бы
    из интерфейса, оставшись в базе."""

    def post(self, request, folder_id):
        folder = get_object_or_404(ExchangeFolder, pk=folder_id)
        if not folder.can_be_deleted_by(request.user):
            raise PermissionDenied

        source_owner_id = folder.owner_id
        old_parent = folder.parent
        new_owner, new_parent = _move_destination(
            request, default_owner_id=source_owner_id, folder_param='parent_id',
        )

        if new_parent is not None and (
            new_parent.pk == folder.pk or is_descendant(new_parent, folder)
        ):
            return JsonResponse(
                {'success': False,
                 'error': 'Нельзя перенести папку в саму себя или во вложенную папку'},
                status=400,
            )

        with transaction.atomic():
            folder.parent = new_parent
            folder.owner = new_owner
            folder.save(update_fields=['parent', 'owner'])

            if new_owner.pk != source_owner_id:
                subtree_ids = folder_subtree_ids(folder)
                ExchangeFolder.objects.filter(pk__in=subtree_ids).update(owner=new_owner)
                # Без is_deleted=False: у лежащего в корзине файла folder
                # сохранён, и восстановление вернёт его в эту же папку. Не
                # смени ему владельца — вернулся бы файл, невидимый ни в
                # одной папке.
                ExchangeFile.objects.filter(folder_id__in=subtree_ids).update(owner=new_owner)

        _notify_folder(old_parent, source_owner_id, actor=request.user,
                       action='folder_moved', text=f'перенёс папку «{folder.name}»')
        if (source_owner_id, old_parent.pk if old_parent else None) != (
            new_owner.pk, new_parent.pk if new_parent else None
        ):
            _notify_folder(new_parent, new_owner.pk, actor=request.user,
                           action='folder_moved', text=f'перенёс сюда папку «{folder.name}»')

        if new_owner.pk != source_owner_id:
            _notify_new_owner(
                new_owner, actor=request.user,
                text=f'перенёс папку «{folder.name}» в вашу папку обменника',
            )

        return JsonResponse({'success': True})



class RenameExchangeFileView(ExchangeFileMixin, RenameObjectView):
    """Имя лежит в FileObject.original_name — своего заголовка у
    ExchangeFile нет."""

    name_param = 'name'
    empty_error = 'Укажите название файла'

    def apply_name(self, obj, name):
        obj.file_object.original_name = name
        obj.file_object.save(update_fields=['original_name'])


class MoveExchangeFileView(LoginRequiredMixin, View):
    """Перемещение файла в любую папку обменника, включая чужую личную.

    Перенос в чужую папку — смена `owner`, а не только `folder`: список
    фильтруется по обоим полям, и файл со старым владельцем не был бы виден
    никому. Отсюда принятое последствие: вместе с владельцем переходит право
    удаления, а владельца папки-назначения уведомляем, как при загрузке.
    """

    def post(self, request, file_id):
        exchange_file = get_object_or_404(ExchangeFile, pk=file_id, is_deleted=False)
        if not exchange_file.can_be_deleted_by(request.user):
            raise PermissionDenied

        source_owner_id = exchange_file.owner_id
        source_folder = exchange_file.folder
        new_owner, folder = _move_destination(request, default_owner_id=source_owner_id)

        exchange_file.owner = new_owner
        exchange_file.folder = folder
        exchange_file.save(update_fields=['owner', 'folder'])

        # Обновиться должны обе папки: и та, откуда файл ушёл, и та, куда
        # приехал — иначе у смотрящего на папку-источник он так и висел бы.
        # С переносом между сотрудниками это ещё и разные личные папки,
        # поэтому в location идёт владелец до переноса, а не после.
        _notify_folder(source_folder, source_owner_id, actor=request.user,
                       action='file_moved', text='переместил файл')
        if (source_owner_id, source_folder.pk if source_folder else None) != (
            new_owner.pk, folder.pk if folder else None
        ):
            _notify_folder(folder, new_owner.pk, actor=request.user,
                           action='file_moved', text='переместил сюда файл')

        if new_owner.pk != source_owner_id:
            _notify_new_owner(
                new_owner, actor=request.user,
                text='переместил файл в вашу папку обменника',
            )

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


class DownloadExchangeFolderView(LoginRequiredMixin, View):
    """Скачать подпапку обменника целиком одним архивом.

    Содержимое обменника видно всем сотрудникам, поэтому сужать поддерево
    не по чему — как и у одиночного скачивания.
    """

    def get(self, request, folder_id):
        folder = get_object_or_404(ExchangeFolder, pk=folder_id)
        paths = folder_subtree(folder)

        files = ExchangeFile.objects.filter(
            folder_id__in=paths, is_deleted=False,
        ).select_related('file_object__blob')

        return fm_folder_archive_view(
            request,
            items=[(paths[item.folder_id], item.file_object) for item in files],
            filename=f'{folder.name}.zip',
        )


class DownloadExchangePersonalFolderView(LoginRequiredMixin, View):
    """Скачать личную папку сотрудника целиком.

    Отдельная вьюха, потому что папка верхнего уровня в обменнике — это сам
    сотрудник, а не запись ExchangeFolder: у файлов в корне личной папки
    folder=None, и через DownloadExchangeFolderView они недостижимы в
    принципе.
    """

    def get(self, request, user_id):
        owner = get_object_or_404(get_user_model(), pk=user_id)

        paths = {None: ()}
        for root in ExchangeFolder.objects.filter(owner=owner, parent__isnull=True):
            paths.update(folder_subtree(root))

        files = ExchangeFile.objects.filter(
            owner=owner, is_deleted=False,
        ).select_related('file_object__blob')

        return fm_folder_archive_view(
            request,
            items=[(paths.get(item.folder_id, ()), item.file_object) for item in files],
            filename=f'{owner.get_full_name() or owner.username}.zip',
        )


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
    """Массовое перемещение — по тем же правилам, что и поштучное
    (MoveExchangeFileView), включая перенос в личную папку другого
    сотрудника со сменой владельца.

    Раньше массового переноса у обменника не было вовсе — в панели
    выделения оставалось только удаление, хотя выделять файлы пачкой уже
    было можно. Затем он повторял тогдашний запрет на чужую папку; теперь
    запрет снят в обеих вьюхах сразу — разойдись они, массовая операция
    стала бы обходным путём вокруг поштучной или наоборот."""

    def post(self, request):
        file_ids = request.POST.getlist('file_ids')
        if not file_ids:
            return JsonResponse({'success': False, 'error': 'Ничего не выбрано'}, status=400)

        # default_owner_id=None: у выделения нет одного «текущего владельца»,
        # поэтому без явного owner_id и без папки владелец не меняется
        # вообще — файлы просто переезжают в корень своей же личной папки.
        new_owner, folder = _move_destination(request, default_owner_id=None)

        allowed = list(
            ExchangeFile.objects.filter(
                Q(owner=request.user) | Q(uploaded_by=request.user),
                pk__in=file_ids, is_deleted=False,
            ).values_list('pk', 'owner_id', 'folder_id')
        )
        if not allowed:
            # 200 с сообщением, а не 403 — см. BulkTrashExchangeFilesView.
            return JsonResponse({
                'success': True, 'task_id': None, 'done': 0,
                'message': 'Ничего не перемещено: нет прав на выбранные файлы',
            })

        from storage.tasks import bulk_move_documents

        task = bulk_move_documents.delay(
            'exchange', 'ExchangeFile', [pk for pk, _, _ in allowed],
            'folder_id', folder.pk if folder else None,
            extra_updates={'owner_id': new_owner.pk} if new_owner else None,
        )

        # Обновить надо и папки-источники, и папку-назначение. Источники
        # раньше не обновлялись вовсе — файл продолжал висеть у соседа,
        # смотрящего на ту папку, откуда его унесли.
        target_folder_id = folder.pk if folder else None
        locations = {(owner_id, folder_id) for _, owner_id, folder_id in allowed}
        if new_owner is not None:
            locations.add((new_owner.pk, target_folder_id))
        else:
            locations |= {(owner_id, target_folder_id) for _, owner_id, _ in allowed}

        for owner_id, folder_id in locations:
            realtime.broadcast(
                realtime.SCOPE_EXCHANGE, realtime.exchange_location(owner_id, folder_id),
                action='files_moved', actor=request.user, text='переместил файлы',
            )

        if new_owner is not None and any(owner_id != new_owner.pk for _, owner_id, _ in allowed):
            moved = len(allowed)
            _notify_new_owner(
                new_owner, actor=request.user,
                text='переместил файлы в вашу папку обменника'
                     + (f' ({moved} шт.)' if moved > 1 else ''),
            )

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
        except QuotaExceededError as error:
            return JsonResponse({'success': False, 'error': str(error)}, status=400)

        from catalog.models import CatalogDocument

        CatalogDocument.objects.create(
            file_object=copied_object, title=copied_object.original_name, uploaded_by=request.user,
        )

        realtime.broadcast(
            realtime.SCOPE_CATALOG, realtime.folder_location(None),
            action='file_created', actor=request.user, text='добавил документ в каталог',
        )
        return JsonResponse({'success': True})


class DownloadExchangeFileView(ExchangeFileMixin, DownloadObjectView):
    """Скачивать может любой сотрудник, поэтому проверка прав снимается."""

    def check_permission(self, request, obj):
        pass


class TrashExchangeFileView(ExchangeFileMixin, TrashObjectView):
    pass


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


class RestoreExchangeFileView(ExchangeFileMixin, RestoreObjectView):
    pass


class PurgeExchangeFileView(ExchangeFileMixin, PurgeObjectView):
    pass
