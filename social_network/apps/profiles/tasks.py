"""Периодические задачи profiles: срок жизни вложений переписки.

Очистка живёт здесь, а не в storage: storage отдаёт только политику
(get_category_ttl_days) и корректно отрабатывает detach(), а знать, что
запись MessageAttachment можно удалять по возрасту, — дело потребителя
(ARCHITECTURE.md, раздел 1). Тот же приём, что в
exchange.tasks.cleanup_expired_exchange_files.
"""
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from storage.models import FileObject
from storage.services import StorageService

from .models import MessageAttachment

CONSUMER = 'profiles.MessageAttachment:expired'


@shared_task
def cleanup_expired_chat_attachments():
    """Снимает файлы с сообщений, которым больше срока хранения категории
    «Вложение чата» (по умолчанию 7 дней, правится в админке).

    Сама запись остаётся: сообщение с вложением, но без файла, обязано
    объяснить, куда файл делся, иначе в переписке оказался бы пустой пузырь.
    """
    ttl_days = StorageService.get_category_ttl_days(FileObject.Category.CHAT)
    if ttl_days is None:
        return 0

    deadline = timezone.now() - timedelta(days=ttl_days)
    expired = MessageAttachment.objects.filter(
        created__lte=deadline, file_object__isnull=False,
    ).select_related('file_object')

    purged = 0
    for attachment in expired:
        file_object = attachment.file_object

        # Ссылка снимается ДО detach(): detach() защищает файлы, на которые
        # ещё кто-то ссылается, и с непогашенным полем не удалил бы ничего,
        # но отчитался бы об успехе (SESSION_CONTEXT, баг №3).
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            file_object=None, expired_at=timezone.now(),
        )
        StorageService.detach(file_object, consumer=CONSUMER)
        purged += 1

    return purged
