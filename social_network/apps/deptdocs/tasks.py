from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from storage.signals import attribute_deletion

from .models import DepartmentDocument

TRASH_RETENTION_DAYS = getattr(settings, 'STORAGE_TRASH_RETENTION_DAYS', 30)


@shared_task
def cleanup_deptdocs_trash():
    """Окончательно удаляет документы отдела, пролежавшие в корзине дольше
    STORAGE_TRASH_RETENTION_DAYS. См. catalog.tasks.cleanup_catalog_trash —
    та же логика, отдельная задача, потому что модуль не должен трогать
    чужие таблицы."""
    deadline = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    expired = DepartmentDocument.objects.filter(is_deleted=True, deleted_at__lte=deadline)

    purged = 0
    for document in expired:
        attribute_deletion(document, consumer='deptdocs.DepartmentDocument:trash_expired')
        document.delete()
        purged += 1

    return purged
