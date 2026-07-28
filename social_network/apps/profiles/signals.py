import nh3
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from django_private_chat2.models import MessageModel

from .models import Task, Event
from .views import clear_user_cache

ALLOWED_MESSAGE_TAGS = {'b', 'i', 'u', 's'}


@receiver(post_save, sender=MessageModel)
def clear_recipient_unread_cache(sender, instance, **kwargs):
    clear_user_cache(instance.recipient_id)


@receiver(pre_save, sender=MessageModel)
def sanitize_message_text(sender, instance, **kwargs):
    if instance.text:
        instance.text = nh3.clean(instance.text, tags=ALLOWED_MESSAGE_TAGS, attributes={})


# Задачи с дедлайном отображаются в календаре напрямую через
# CalendarEventsFeedView (без создания дублирующей записи Event).
