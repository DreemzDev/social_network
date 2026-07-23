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