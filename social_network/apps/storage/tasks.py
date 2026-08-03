from celery import shared_task

from .services import StorageService


@shared_task
def cleanup_orphan_files():
    """Периодическая задача: физически удаляет blob-файлы, пробывшие в
    ORPHAN дольше STORAGE_ORPHAN_RETENTION_DAYS."""
    return StorageService.purge_expired_orphans()


@shared_task
def storage_verify(delete_untracked=False):
    """Периодическая задача: сверяет диск с БД. По умолчанию только
    возвращает список расхождений, ничего не удаляет."""
    untracked = StorageService.find_untracked_files()

    if delete_untracked:
        import os
        for path in untracked:
            os.remove(path)

    return {'untracked_count': len(untracked), 'deleted': delete_untracked}
