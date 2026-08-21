from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import JsonResponse
from django.views.generic import TemplateView, View

from . import limits
from .models import FileObject
from .services import StorageService
from .utils import fm_task_response, owns_task

# Сколько записей корзины показывать на сводной странице каждого таба.
# Корзина каталога общая для всего портала, поэтому без ограничения эта
# страница отрисовывала бы все удалённые документы разом — у отдельных
# TrashView каждого модуля paginate_by=24 был, а у сводной не было вовсе.
TRASH_PREVIEW_LIMIT = 48


class StorageDashboardView(LoginRequiredMixin, TemplateView):
    """Занятое место и корзины всех модулей файлового менеджера в одном
    месте — раньше корзину чистили только вручную по одному файлу, и не
    было способа узнать, сколько места вообще занято."""

    template_name = 'storage/dashboard.html'

    def get_context_data(self, **kwargs):
        from catalog.models import CatalogDocument
        from deptdocs.models import DepartmentDocument
        from exchange.models import ExchangeFile

        context = super().get_context_data(**kwargs)
        user = self.request.user
        quota = limits.user_quota()

        usage = StorageService.get_usage(user)
        context['my_usage'] = usage
        context['my_quota'] = quota
        # min(…, 100) — иначе при превышении квоты (файлы могли быть
        # загружены до её появления в настройках) ширина полосы уезжала
        # за пределы контейнера.
        context['my_usage_percent'] = min(round(usage * 100 / quota), 100) if quota else None

        if user.is_staff:
            context['storage_stats'] = StorageService.get_storage_stats()

        context['trash_retention_days'] = limits.trash_retention_days()
        context['exchange_ttl_days'] = StorageService.get_category_ttl_days(
            FileObject.Category.EXCHANGE
        )
        context['trash_summary'] = [
            {
                'label': 'Обменник',
                # Считаем ровно то, что пользователь увидит в своей корзине.
                # Раньше здесь стоял счётчик по ВСЕМ пользователям, а
                # корзина показывает только свои файлы — карточка обещала
                # «12 файлов», а внутри их оказывалось два.
                'count': ExchangeFile.objects.filter(
                    Q(deleted_by=user) | Q(owner=user), is_deleted=True,
                ).count(),
                'module': 'exchange',
                'note': 'файлы обменника удаляются автоматически через '
                        f'{StorageService.get_category_ttl_days(FileObject.Category.EXCHANGE)} дн. '
                        'после загрузки — отдельной автоочистки корзины не требуется',
            },
            {
                'label': 'Информационный каталог',
                'count': CatalogDocument.objects.filter(is_deleted=True).count(),
                'module': 'catalog',
            },
            {
                'label': 'Приватный доступ',
                'count': DepartmentDocument.objects.filter(
                    is_deleted=True, folder__allowed_users=user,
                ).distinct().count(),
                'module': 'deptdocs',
            },
        ]
        return context


class UnifiedTrashView(LoginRequiredMixin, TemplateView):
    """Одна страница с корзинами всех трёх модулей файлового менеджера,
    переключаемыми табами.

    До этой страницы у каждого модуля была отдельная корзина по отдельному
    адресу (/exchange/trash/, /catalog/trash/, /deptdocs/trash/), и с виду
    все три выглядят одинаково — общий layout fm-scope, тот же заголовок
    «Корзина» в хлебной крошке. Легко открыть не ту корзину и решить, что
    удалённый файл пропал, хотя он лежит в корзине другого модуля.

    Каждый queryset — та же логика фильтрации, что и в отдельных
    TrashView соответствующего модуля (кто что видит), просто собранная в
    одном месте. Кнопки восстановить/удалить окончательно ведут на те же
    существующие эндпоинты модулей — эта вьюха не дублирует их логику."""

    template_name = 'storage/trash.html'

    def get_context_data(self, **kwargs):
        from catalog.models import CatalogDocument
        from deptdocs.models import DepartmentDocument
        from exchange.models import ExchangeFile

        context = super().get_context_data(**kwargs)
        user = self.request.user

        exchange_qs = ExchangeFile.objects.filter(
            Q(deleted_by=user) | Q(owner=user), is_deleted=True,
        ).select_related('file_object', 'file_object__blob', 'uploaded_by').order_by('-deleted_at')

        catalog_qs = CatalogDocument.objects.filter(
            is_deleted=True
        ).select_related('file_object', 'file_object__blob', 'uploaded_by').order_by('-deleted_at')

        deptdocs_qs = DepartmentDocument.objects.filter(
            is_deleted=True, folder__allowed_users=user,
        ).distinct().select_related(
            'file_object', 'file_object__blob', 'uploaded_by'
        ).order_by('-deleted_at')

        context['tabs'] = [
            self._tab('exchange', 'Обменник', exchange_qs,
                      'includes/fm/exchange_file_card.html', 'exchange_trash',
                      'Корзина обменника пуста'),
            self._tab('catalog', 'Информационный каталог', catalog_qs,
                      'includes/fm/catalog_document_card.html', 'catalog_trash',
                      'Корзина каталога пуста'),
            self._tab('deptdocs', 'Приватный доступ', deptdocs_qs,
                      'includes/fm/deptdoc_card.html', 'deptdocs_trash',
                      'Корзина приватного доступа пуста'),
        ]

        context['active_tab'] = self.request.GET.get('tab', 'exchange')
        context['trash_retention_days'] = limits.trash_retention_days()
        context['exchange_ttl_days'] = StorageService.get_category_ttl_days(
            FileObject.Category.EXCHANGE
        )
        return context

    @staticmethod
    def _tab(key, label, queryset, card_template, full_url, empty_text):
        total = queryset.count()
        return {
            'key': key,
            'label': label,
            'count': total,
            'items': list(queryset[:TRASH_PREVIEW_LIMIT]),
            'truncated': total > TRASH_PREVIEW_LIMIT,
            'card': card_template,
            'full_url': full_url,
            'empty_text': empty_text,
        }


class TaskStatusView(LoginRequiredMixin, View):
    """Опрос статуса фоновой задачи (массовое перемещение/удаление) по
    task_id, который вернул запускающий эндпоинт. Общая точка для всех
    трёх модулей — прогресс-бар на фронте не знает и не должен знать,
    какой модуль его запустил, ему нужен только {state, done, total}.

    Polling, а не WebSocket: массовая операция — разовое действие с
    коротким временем жизни (секунды-десятки секунд для сотен файлов),
    заводить для этого отдельный WS-канал избыточно, когда уже есть
    штатный celery.result.AsyncResult с тем же результатом.

    Отдаётся только задача, запущенная этой же сессией (см.
    utils.fm_task_response). Раньше статус получал любой аутентифицированный
    по чужому task_id: угадать uuid4 нельзя, но идентификатор — не секрет,
    он виден в трафике и в логах, а владения не проверялось вообще."""

    def get(self, request, task_id):
        from celery.result import AsyncResult

        if not owns_task(request, task_id):
            # 404, а не 403: чужой task_id для этой сессии не существует,
            # и подтверждать его существование незачем.
            return JsonResponse({'error': 'Задача не найдена'}, status=404)

        result = AsyncResult(task_id)
        payload = {'state': result.state}

        if result.state == 'PROGRESS':
            payload.update(result.info if isinstance(result.info, dict) else {})
        elif result.state == 'SUCCESS':
            # Массовые операции возвращают dict {'done': …, 'total': …}, а
            # задачи очистки корзины — просто число удалённых записей.
            # Раньше здесь стоял безусловный payload.update(result.result),
            # который на числовом результате падал с TypeError: пока
            # очистка вызывалась синхронно, это не проявлялось.
            if isinstance(result.result, dict):
                payload.update(result.result)
            else:
                payload['result'] = result.result
        elif result.state == 'FAILURE':
            payload['error'] = str(result.info)

        return JsonResponse(payload)


class RunTrashCleanupView(LoginRequiredMixin, View):
    """Ручной запуск автоочистки корзины немедленно, не дожидаясь ночного
    расписания Celery beat. Доступно только персоналу: массовое удаление
    затрагивает записи всех пользователей, а не только свои.

    Задача ставится в очередь (.delay()), а не вызывается синхронно.
    Раньше вьюха звала cleanup_catalog_trash() прямо в HTTP-потоке, и та
    удаляла записи по одной, с detach() на каждой: на непустой корзине
    запрос висел до конца очистки. Ответ отдаёт task_id, а прогресс
    показывается тем же механизмом, что и у массовых операций
    (TaskStatusView)."""

    TASKS = {
        'catalog': 'catalog.tasks.cleanup_catalog_trash',
        'deptdocs': 'deptdocs.tasks.cleanup_deptdocs_trash',
        'exchange': 'exchange.tasks.cleanup_expired_exchange_files',
    }

    def post(self, request, module):
        if not request.user.is_staff:
            return JsonResponse({'success': False, 'error': 'Недостаточно прав'}, status=403)

        if module not in self.TASKS:
            return JsonResponse({'success': False, 'error': 'Неизвестный модуль'}, status=400)

        if module == 'catalog':
            from catalog.tasks import cleanup_catalog_trash as task
        elif module == 'deptdocs':
            from deptdocs.tasks import cleanup_deptdocs_trash as task
        else:
            from exchange.tasks import cleanup_expired_exchange_files as task

        return fm_task_response(request, task.delay())
