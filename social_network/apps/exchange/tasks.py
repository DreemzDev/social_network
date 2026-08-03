from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .models import ExchangeFile


@shared_task
def cleanup_expired_exchange_files():
    """Удаляет файлы обменника старше срока хранения категории — аналог
    очистки старых сетевых папок-обменников по расписанию.

    Очистка живёт здесь, а не в storage: storage не знает, что запись
    ExchangeFile можно удалять по возрасту, и не должен трогать чужие
    таблицы. Его задача — только отдать политику (get_category_ttl_days) и
    корректно отработать detach() (ARCHITECTURE.md, раздел 1).

    Файлы в корзине тоже удаляются: попадание в корзину не продлевает срок
    хранения, иначе обменник можно засорить, просто ничего не восстанавливая.
    """
    ttl_days = StorageService.get_category_ttl_days(FileObject.Category.EXCHANGE)
    if ttl_days is None:
        return 0

    deadline = timezone.now() - timedelta(days=ttl_days)
    expired = ExchangeFile.objects.filter(uploaded_at__lte=deadline).select_related('file_object')

    purged = 0
    for exchange_file in expired:
        # Удаляем по одной записи, а не queryset.delete(): detach() выполняет
        # сигнал post_delete на конкретном экземпляре, и там же читается
        # пометка о причине удаления.
        attribute_deletion(exchange_file, consumer='exchange.ExchangeFile:expired')
        exchange_file.delete()
        purged += 1

    return purged
