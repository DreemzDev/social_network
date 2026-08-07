"""Общие импорты и хелперы, используемые несколькими views-модулями."""
import logging

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.db.models import Q
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from ..models import Notification

logger = logging.getLogger(__name__)


def clear_user_cache(user_pk):
    """Централизованная очистка кеша непрочитанных сообщений пользователя."""
    cache.delete(f'unread_count_{user_pk}')
    cache.delete(f'dialogs_unread_{user_pk}')


def push_chat_event(user_id, event_type, **payload):
    """Отправляет событие в Channels-группу пользователя (группа — просто
    str(user_id), см. apps/profiles/consumers.py:ExtendedChatConsumer).

    Недоступность брокера НЕ роняет операцию. Все вызывающие места устроены
    одинаково: сообщение/реакция/уведомление уже записаны в БД, а это —
    доставка «живьём». Если Redis лёг, исключение отсюда превращало
    успешно выполненное действие в 500: у отправителя ошибка, в базе
    сообщение есть, повтор создаёт дубликат. Получатель в этом случае
    просто увидит изменения при следующем открытии диалога — ровно как до
    появления WebSocket.

    Тот же приём и по той же причине — в storage/realtime.py: broadcast().
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:  # окружение без CHANNEL_LAYERS
        return

    try:
        async_to_sync(channel_layer.group_send)(str(user_id), {'type': event_type, **payload})
    except Exception:
        logger.warning(
            'Не удалось доставить событие %s пользователю %s', event_type, user_id, exc_info=True,
        )


def notify(recipient, kind, text, actor=None, task=None, post=None, url=''):
    """Создаёт запись уведомления и пушит её получателю через тот же канал,
    что уже используется чатом.

    url — готовая ссылка для типов без своего FK-поля (например,
    FILE_SHARED: файл может быть в обменнике, каталоге или приватном
    доступе, заводить FK на все три модуля ради одной ссылки избыточно)."""
    notification = Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, text=text, task=task, post=post, url=url,
    )
    unread_count = Notification.objects.filter(recipient=recipient, is_read=False).count()

    push_chat_event(recipient.id, 'notification', notification={
        'id': notification.id,
        'kind': notification.kind,
        'text': notification.text,
        'created': notification.created.isoformat(),
        'task_id': task.id if task else None,
        'post_id': post.id if post else None,
        'url': url or None,
    }, unread_count=unread_count)

    return notification
