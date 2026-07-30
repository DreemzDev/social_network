from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin

from ..models import Task, Notification
from ..forms import TaskForm, TaskEditForm
from ._common import notify


def _serialize_task(task, with_urls=False):
    data = {
        'id': task.id,
        'title': task.title,
        'description': task.description,
        'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
        'due_date_iso': task.due_date.isoformat() if task.due_date else '',
        'is_completed': task.is_completed,
        'status': task.status,
        'status_label': task.get_status_display(),
    }
    if with_urls:
        data.update({
            'toggle_url': reverse('task_toggle', kwargs={'task_id': task.id}),
            'delete_url': reverse('task_delete', kwargs={'task_id': task.id}),
            'edit_url': reverse('task_edit', kwargs={'task_id': task.id}),
        })
    return data


def _task_participant_or_404(task_id, user):
    """Задача видна и исполнителю, и постановщику (если это разные люди)."""
    return get_object_or_404(Task.objects.filter(Q(user=user) | Q(assigned_by=user)), pk=task_id)


def _task_creator_or_404(task_id, user):
    """Изменять/удалять задачу может только тот, кто её создал: постановщик
    (если поручена) либо сам исполнитель (для личных задач без постановщика)."""
    task = get_object_or_404(Task, pk=task_id)
    creator = task.assigned_by or task.user
    if creator != user:
        raise Http404
    return task


class TaskListFeedView(LoginRequiredMixin, View):
    """Список собственных задач пользователя (без поручений от других) —
    используется для точечного обновления виджета «Мои задачи», когда
    задача была изменена из другого места (например, из виджета календаря)."""

    def get(self, request):
        own_tasks = Task.objects.filter(user=request.user).filter(Q(assigned_by__isnull=True) | Q(assigned_by=request.user))
        return JsonResponse({
            'pending': [_serialize_task(t) for t in own_tasks.filter(is_completed=False)],
            'completed': [_serialize_task(t) for t in own_tasks.filter(is_completed=True)],
        })


class TaskCreateView(LoginRequiredMixin, View):
    def post(self, request):
        form = TaskForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        task = form.save(commit=False)
        task.user = request.user
        task.save()
        return JsonResponse({'success': True, 'task': _serialize_task(task)})


class TaskQuickCreateView(LoginRequiredMixin, View):
    """Быстрое создание задачи из заметки (AJAX): название берётся из
    выделенного в заметке текста, либо из первых 100 символов заметки."""

    def post(self, request):
        title = request.POST.get('title', '').strip()[:255]
        if not title:
            return JsonResponse({'success': False, 'error': 'Пустое название задачи.'}, status=400)

        task = Task.objects.create(user=request.user, title=title)
        return JsonResponse({'success': True, 'task': _serialize_task(task, with_urls=True)})


class TaskStatusUpdateView(LoginRequiredMixin, View):
    """Меняет статус задачи. Доступно и исполнителю, и постановщику — оба видят
    один и тот же статус (синхронизирован через единое поле в модели)."""

    def post(self, request, task_id):
        task = _task_participant_or_404(task_id, request.user)
        status = request.POST.get('status')
        if status not in dict(Task.Status.choices):
            return JsonResponse({'success': False, 'error': 'Некорректный статус.'}, status=400)

        task.status = status
        task.save(update_fields=['status', 'is_completed'])

        # Постановщика уведомляем о смене статуса только если статус меняет
        # именно исполнитель — иначе постановщик уведомлял бы сам себя.
        if task.assigned_by_id and task.assigned_by_id != request.user.id:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                task.assigned_by, Notification.Kind.TASK_STATUS_CHANGED,
                f'{actor_name} изменил(а) статус задачи «{task.title}» на «{task.get_status_display()}»',
                actor=request.user, task=task,
            )

        return JsonResponse({'success': True, 'task': _serialize_task(task, with_urls=True)})


class TaskToggleView(LoginRequiredMixin, View):
    """Переключение выполнено/не выполнено (чекбокс в виджете «Мои задачи»).
    Доступно и исполнителю, и постановщику."""

    def post(self, request, task_id):
        task = _task_participant_or_404(task_id, request.user)
        task.status = Task.Status.NOT_STARTED if task.is_completed else Task.Status.DONE
        task.save(update_fields=['status', 'is_completed'])
        return JsonResponse({'success': True, 'task': _serialize_task(task, with_urls=True)})


class TaskDeleteView(LoginRequiredMixin, View):
    def post(self, request, task_id):
        _task_creator_or_404(task_id, request.user).delete()
        return JsonResponse({'success': True})


class TaskEditView(LoginRequiredMixin, View):
    """Редактировать задачу может только постановщик (или сам исполнитель для
    личных задач без постановщика). При изменении помечает задачу как
    is_edited, чтобы исполнитель видел, что условия поменялись."""

    def post(self, request, task_id):
        task = _task_creator_or_404(task_id, request.user)

        form = TaskEditForm(request.POST, instance=task)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        task = form.save(commit=False)
        is_assignment = task.assigned_by_id and task.assigned_by_id != task.user_id
        if is_assignment:
            task.is_edited = True
        task.save()

        if is_assignment:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                task.user, Notification.Kind.TASK_EDITED,
                f'{actor_name} изменил(а) задачу «{task.title}»',
                actor=request.user, task=task,
            )

        data = _serialize_task(task)
        data['is_edited'] = task.is_edited
        return JsonResponse({'success': True, 'task': data})
