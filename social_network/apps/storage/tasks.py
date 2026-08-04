from celery import shared_task

from .services import StorageService


@shared_task(bind=True)
def bulk_trash_documents(self, app_label, model_name, doc_ids, user_id, extra_filter=None):
    """Массовое перемещение документов/файлов в корзину в фоне.

    Синхронный queryset.update() был бы достаточен для десятка файлов, но
    при выборе сотен документов (например, вся папка целиком) блокирующий
    HTTP-запрос держит соединение и не даёт пользователю никакой обратной
    связи, пока не отработает весь update(). Celery превращает это в
    "запустили → проверяем статус", с прогрессом по мере обработки партий.

    app_label/model_name, а не сама модель — задача сериализуется через
    Redis, импортировать модель по имени безопаснее, чем пытаться передать
    класс. extra_filter — доп. ограничение queryset (например, только
    доступные текущему пользователю документы), передаётся как dict.
    """
    from django.apps import apps
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    model = apps.get_model(app_label, model_name)
    user = get_user_model().objects.get(pk=user_id)

    filters = {'pk__in': doc_ids, 'is_deleted': False}
    if extra_filter:
        filters.update(extra_filter)

    queryset = model._default_manager.filter(**filters)
    total = queryset.count()

    trashed = 0
    batch_size = 200
    ids_to_process = list(queryset.values_list('pk', flat=True))

    for start in range(0, len(ids_to_process), batch_size):
        batch_ids = ids_to_process[start:start + batch_size]
        trashed += model._default_manager.filter(pk__in=batch_ids).update(
            is_deleted=True, deleted_at=timezone.now(), deleted_by=user,
        )
        self.update_state(state='PROGRESS', meta={'done': trashed, 'total': total})

    return {'done': trashed, 'total': total}


@shared_task(bind=True)
def bulk_move_documents(self, app_label, model_name, doc_ids, folder_field, folder_id, extra_filter=None):
    """Массовое перемещение в другую папку в фоне — тот же принцип
    батчинга и прогресса, что и в bulk_trash_documents."""
    from django.apps import apps

    model = apps.get_model(app_label, model_name)

    filters = {'pk__in': doc_ids, 'is_deleted': False}
    if extra_filter:
        filters.update(extra_filter)

    queryset = model._default_manager.filter(**filters)
    total = queryset.count()

    moved = 0
    batch_size = 200
    ids_to_process = list(queryset.values_list('pk', flat=True))

    for start in range(0, len(ids_to_process), batch_size):
        batch_ids = ids_to_process[start:start + batch_size]
        moved += model._default_manager.filter(pk__in=batch_ids).update(**{folder_field: folder_id})
        self.update_state(state='PROGRESS', meta={'done': moved, 'total': total})

    return {'done': moved, 'total': total}


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
