from datetime import date, timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from django_private_chat2.models import MessageModel, DialogsModel
from django.core.cache import cache
from django.db.models import Q, Count


def birthday_notifications(request):
    """Context processor для уведомлений о днях рождения"""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    User = get_user_model()
    
    birthday = User.objects.filter(
        birthday__day=today.day,
        birthday__month=today.month
    )
    
    delta_birthday = User.objects.filter(
        birthday__day=tomorrow.day,
        birthday__month=tomorrow.month
    )
    
    return {
        'birthday': birthday,
        'delta_birthday': delta_birthday
    }


def notification_center(request):
    """Собирает содержимое колокольчика уведомлений: сохранённые уведомления
    о задачах (поставили/изменили/сменили статус) + дедлайны, посчитанные на
    лету (без фонового планировщика — считаем при каждом открытии страницы),
    + дни рождения. Хранимые уведомления читает 'own_or_recipient=True' —
    т.е. только те, что реально для этого пользователя."""
    if not request.user.is_authenticated:
        return {'notification_center_items': [], 'notification_center_unread': 0}

    from .models import Task, Notification

    user = request.user
    today = date.today()

    # Прочитанные уведомления старше недели больше не нужны — чистим при
    # каждом заходе, без фонового планировщика (тот же подход, что и для
    # дедлайнов ниже).
    Notification.objects.filter(
        recipient=user, is_read=True, created__lt=timezone.now() - timedelta(days=7)
    ).delete()

    stored = Notification.objects.filter(recipient=user).select_related('actor', 'task').order_by('-created')[:20]
    unread_count = Notification.objects.filter(recipient=user, is_read=False).count()

    items = []
    for n in stored:
        if n.task_id:
            url = f'/profile/{n.task.user.username}/'
        elif n.post_id:
            url = f'/post/{n.post_id}/'
        else:
            url = None
        items.append({
            'kind': n.kind,
            'text': n.text,
            'created': n.created,
            'is_read': n.is_read,
            'id': n.id,
            'url': url,
        })

    # Дедлайны считаем на лету: свои задачи (или поставленные другим) с
    # due_date в ближайшие 2 дня или уже просроченные, ещё не выполненные.
    soon = today + timedelta(days=2)
    deadline_tasks = Task.objects.filter(
        Q(user=user) | Q(assigned_by=user)
    ).filter(due_date__isnull=False, due_date__lte=soon).exclude(status=Task.Status.DONE)

    for t in deadline_tasks:
        if t.due_date < today:
            text = f'Просрочена задача «{t.title}» (срок был {t.due_date.strftime("%d.%m")})'
        elif t.due_date == today:
            text = f'Задача «{t.title}» — срок сегодня'
        else:
            text = f'Задача «{t.title}» — срок {t.due_date.strftime("%d.%m")}'
        items.append({
            'kind': 'deadline',
            'text': text,
            'created': None,
            'is_read': True,  # дедлайны не хранятся и не считаются в счётчике непрочитанных
            'id': None,
            'url': f'/profile/{t.user.username}/',
        })

    return {'notification_center_items': items, 'notification_center_unread': unread_count}


def online_users(request):
    """Context processor со списком онлайн-коллег для виджета в правой колонке"""
    if not request.user.is_authenticated:
        return {'widget_online_users': []}

    User = get_user_model()
    time_threshold = timezone.now() - timedelta(minutes=5)
    users = User.objects.filter(
        last_activity__gte=time_threshold
    ).exclude(pk=request.user.pk)[:10]

    return {'widget_online_users': users}


def unread_messages_count(request):
    """
    Context processor для автоматического добавления количества 
    непрочитанных сообщений во все шаблоны
    """
    if not request.user.is_authenticated:
        return {'unread_messages_count': 0}
    
    # Используем кеш для оптимизации
    cache_key = f'unread_count_{request.user.pk}'
    unread_count = cache.get(cache_key)
    
    if unread_count is None:
        unread_count = MessageModel.objects.filter(
            recipient=request.user,
            read=False
        ).count()
        
        # Кешируем на 30 секунд (WebSocket обновляет в реальном времени)
        cache.set(cache_key, unread_count, 30)
    
    return {'unread_messages_count': unread_count}


def get_dialogs_unread_counts(user):
    """
    Вспомогательная функция для получения непрочитанных по диалогам
    Используется в views и WebSocket обработчиках
    """
    cache_key = f'dialogs_unread_{user.pk}'
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    # Получаем все диалоги пользователя
    dialogs = DialogsModel.objects.filter(
        Q(user1=user) | Q(user2=user)
    ).select_related('user1', 'user2')
    
    # Получаем всех собеседников
    other_user_ids = []
    for dialog in dialogs:
        other_user = dialog.user1 if dialog.user2 == user else dialog.user2
        other_user_ids.append(other_user.pk)
    
    # Один запрос для подсчета непрочитанных от всех собеседников
    dialogs_unread = {}
    if other_user_ids:
        unread_counts = MessageModel.objects.filter(
            sender_id__in=other_user_ids,
            recipient=user,
            read=False
        ).values('sender_id').annotate(count=Count('id'))
        
        for item in unread_counts:
            dialogs_unread[str(item['sender_id'])] = item['count']
    
    # Кешируем на 30 секунд
    cache.set(cache_key, dialogs_unread, 30)
    
    return dialogs_unread