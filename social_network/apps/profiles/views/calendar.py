from datetime import date, timedelta
from functools import lru_cache

from dateutil.relativedelta import relativedelta

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import View
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin

from ..models import Task, Event, Notification
from ..forms import EventForm, TaskEditForm
from ._common import notify
from .tasks import _serialize_task

# Единая палитра маркеров календаря: цвет обозначает тип пункта, а не его
# статус (выполнено/нет) — при выполнении задачи маркер не меняет цвет.
CALENDAR_COLORS = {
    'my_task': '#0077FF',
    'birthday': '#F78B00',
    'assigned_task': '#8B5CF6',
    'reminder': '#14B8A6',
}


class EventCreateView(LoginRequiredMixin, View):
    """Создаёт личное напоминание (AJAX, JSON) — только для себя, без исполнения."""

    def post(self, request):
        form = EventForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        event = form.save(commit=False)
        event.user = request.user
        event.created_by = request.user
        event.event_type = Event.EventType.PERSONAL
        event.save()

        return JsonResponse({'success': True, 'event': {'id': event.id}})


class EventDeleteView(LoginRequiredMixin, View):
    """Удалить событие может только его автор — тот же критерий, по
    которому фид (CalendarEventsFeedView) решает, показывать ли кнопку
    удаления (extendedProps.deletable), независимо от типа события."""

    def post(self, request, event_id):
        get_object_or_404(Event, pk=event_id, created_by=request.user).delete()
        return JsonResponse({'success': True})


class CalendarUsersListView(LoginRequiredMixin, View):
    """Список пользователей для выбора исполнителя задачи через календарь (AJAX)."""

    def get(self, request):
        query = request.GET.get('q', '').strip()
        qs = get_user_model().objects.exclude(pk=request.user.pk)
        if query:
            qs = qs.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(username__icontains=query))
        users = [
            {'id': u.id, 'name': f'{u.first_name} {u.last_name}'.strip() or u.username}
            for u in qs.order_by('first_name', 'last_name')[:20]
        ]
        return JsonResponse({'users': users})


class CalendarTaskCreateView(LoginRequiredMixin, View):
    """Постановка задачи через календарь: себе (без исполнителя) или другому
    сотруднику (с указанием исполнителя и постановщика)."""

    def post(self, request):
        assignee_id = request.POST.get('assignee_id', '').strip()
        assignee = get_object_or_404(get_user_model(), pk=assignee_id) if assignee_id else request.user

        form = TaskEditForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        if not form.cleaned_data.get('due_date'):
            return JsonResponse({'success': False, 'errors': {'due_date': ['Укажите дату.']}}, status=400)

        task = form.save(commit=False)
        task.user = assignee
        task.assigned_by = request.user
        task.save()

        if assignee.id != request.user.id:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                assignee, Notification.Kind.TASK_ASSIGNED,
                f'{actor_name} поставил(а) вам задачу «{task.title}»',
                actor=request.user, task=task,
            )

        data = _serialize_task(task)
        data['assignee_name'] = f'{assignee.first_name} {assignee.last_name}'.strip() or assignee.username
        data['is_own'] = assignee_id == '' or int(assignee_id) == request.user.id
        return JsonResponse({'success': True, 'task': data})


_RECURRENCE_STEP = {
    Event.Recurrence.YEARLY: relativedelta(years=1),
    Event.Recurrence.MONTHLY: relativedelta(months=1),
    Event.Recurrence.WEEKLY: relativedelta(weeks=1),
}


def _expand_recurrence(event, window_start, window_end):
    """Виртуально повторяет событие внутри окна [window_start, window_end],
    не создавая новых записей в БД. Возвращает список дат.

    relativedelta(n * step) считает n-й повтор всегда от исходной
    event.date, а не от даты предыдущего повтора — в отличие от пошагового
    сдвига, это не накапливает ошибку клампинга дня (событие «31 января
    ежемесячно» не «съезжает» на 28/29 число после первого прохода через
    февраль)."""
    if event.recurrence == Event.Recurrence.NONE:
        return [event.date] if window_start <= event.date <= window_end else []

    step = _RECURRENCE_STEP[event.recurrence]
    n = 0
    while event.date + n * step > window_start:
        n -= 1

    dates = []
    occurrence = event.date + n * step
    while occurrence <= window_end:
        if occurrence >= window_start and occurrence >= event.date:
            dates.append(occurrence)
        n += 1
        occurrence = event.date + n * step

    return dates


_TASK_ID_PLACEHOLDER = 0
_TASK_URL_NAMES = ('task_toggle', 'task_status_update', 'task_delete', 'task_edit')


@lru_cache(maxsize=None)
def _task_url_templates():
    """reverse() резолвит urlconf заново на каждый вызов — при большом числе
    задач в фиде это заметный оверхед в цикле. Резолвим URL-шаблон каждого
    имени один раз за весь процесс (с плейсхолдером вместо id) и кэшируем;
    подстановка реального id для конкретной задачи — просто замена строки."""
    return {
        name: reverse(name, kwargs={'task_id': _TASK_ID_PLACEHOLDER})
        for name in _TASK_URL_NAMES
    }


def _task_action_urls(task_id):
    placeholder = str(_TASK_ID_PLACEHOLDER)
    return {
        name: template.replace(placeholder, str(task_id))
        for name, template in _task_url_templates().items()
    }


class CalendarEventsFeedView(LoginRequiredMixin, View):
    """JSON-фид событий для календаря: личные события пользователя + корпоративные + дни рождения."""

    def get(self, request):
        user = request.user
        events = Event.objects.filter(
            Q(user=user, event_type=Event.EventType.PERSONAL) | Q(event_type=Event.EventType.CORPORATE)
        )

        window_start = date.today() - timedelta(days=365)
        window_end = date.today() + timedelta(days=365)

        items = []
        for e in events:
            for occurrence_date in _expand_recurrence(e, window_start, window_end):
                items.append({
                    'id': e.id,
                    'is_task': False,
                    'title': e.title,
                    'description': e.description,
                    'start': occurrence_date.isoformat(),
                    'allDay': True,
                    'color': CALENDAR_COLORS['reminder'],
                    'extendedProps': {
                        'type': e.event_type,
                        'deletable': e.created_by_id == user.id,
                        'isCompleted': False,
                        'isRecurring': e.recurrence != Event.Recurrence.NONE,
                    },
                })

        # Задачи с дедлайном: собственные задачи исполнителя + задачи, которые
        # пользователь поставил другим (постановщик тоже видит их в своём календаре).
        tasks = Task.objects.filter(
            Q(user=user) | Q(assigned_by=user)
        ).filter(due_date__isnull=False).select_related('user', 'assigned_by')

        for t in tasks:
            is_assignment = bool(t.assigned_by_id and t.assigned_by_id != t.user_id)
            is_creator_view = is_assignment and t.assigned_by_id == user.id
            color = CALENDAR_COLORS['assigned_task'] if (is_assignment and not is_creator_view) else CALENDAR_COLORS['my_task']

            assigner_name = ''
            assignee_name = ''
            if is_assignment:
                assigner_name = f'{t.assigned_by.first_name} {t.assigned_by.last_name}'.strip() or t.assigned_by.username
                assignee_name = f'{t.user.first_name} {t.user.last_name}'.strip() or t.user.username

            task_urls = _task_action_urls(t.id)
            items.append({
                'id': t.id,
                'is_task': True,
                'title': t.title,
                'description': t.description,
                'start': t.due_date.isoformat(),
                'allDay': True,
                'color': color,
                'extendedProps': {
                    'type': 'task',
                    'deletable': is_creator_view,
                    'isCompleted': t.is_completed,
                    'isAssignment': is_assignment,
                    'status': t.status,
                    'statusLabel': t.get_status_display(),
                    'isEdited': t.is_edited,
                    'assignerName': assigner_name,
                    'assigneeName': assignee_name if is_creator_view else '',
                    'toggleUrl': task_urls['task_toggle'],
                    'statusUrl': task_urls['task_status_update'],
                    'deleteUrl': task_urls['task_delete'],
                    'editUrl': task_urls['task_edit'],
                    'canEdit': is_creator_view,
                },
            })

        # Дни рождения — виртуальные события без Event-записи, повторяются
        # каждый год. Окно ±365 дней может задевать соседний год (например,
        # в декабре окно уже включает январь следующего года), поэтому
        # порождаем повтор на каждый год, пересекающийся с окном, а не
        # только на текущий.
        for u in get_user_model().objects.exclude(birthday__isnull=True):
            for year in range(window_start.year, window_end.year + 1):
                try:
                    occurrence = u.birthday.replace(year=year)
                except ValueError:
                    occurrence = u.birthday.replace(year=year, day=28)  # 29 февраля
                if not (window_start <= occurrence <= window_end):
                    continue
                items.append({
                    'is_task': False,
                    'title': f'🎂 День рождения у {u.first_name} {u.last_name}',
                    'description': '',
                    'start': occurrence.isoformat(),
                    'allDay': True,
                    'color': CALENDAR_COLORS['birthday'],
                    'extendedProps': {'type': 'birthday', 'deletable': False, 'isCompleted': False},
                })

        return JsonResponse(items, safe=False)
