from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from storage.signals import attribute_deletion

from .models import CatalogDocument

TRASH_RETENTION_DAYS = getattr(settings, 'STORAGE_TRASH_RETENTION_DAYS', 30)


@shared_task
def cleanup_catalog_trash():
    """Окончательно удаляет документы каталога, пролежавшие в корзине
    дольше STORAGE_TRASH_RETENTION_DAYS.

    До этой задачи корзина каталога чистилась только вручную — документ,
    отправленный в корзину, оставался там навсегда, пока кто-то не заходил
    и не удалял его окончательно по одному. Обменник такую очистку уже имел
    (cleanup_expired_exchange_files), только там это очистка по TTL с
    момента загрузки, а не по сроку пребывания в корзине — здесь смысл
    другой: пользователь уже решил удалить файл, автоочистка лишь
    завершает то, что он начал.
    """
    deadline = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    expired = CatalogDocument.objects.filter(is_deleted=True, deleted_at__lte=deadline)

    purged = 0
    for document in expired:
        # По одной записи, а не queryset.delete(): detach() выполняет сигнал
        # post_delete на конкретном экземпляре, и там же читается пометка о
        # причине удаления.
        attribute_deletion(document, consumer='catalog.CatalogDocument:trash_expired')
        document.delete()
        purged += 1

    return purged
