from django.db.models.signals import post_save
from django.dispatch import receiver

from django_private_chat2.models import MessageModel

from .views import clear_user_cache


@receiver(post_save, sender=MessageModel)
def clear_recipient_unread_cache(sender, instance, **kwargs):
    clear_user_cache(instance.recipient_id)
