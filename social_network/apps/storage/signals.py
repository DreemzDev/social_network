"""Автоматический detach() при удалении записи-потребителя.

Каскад (`on_delete=CASCADE`) сносит запись сам, и позвать detach() в этом
случае некому — а пропущенный detach() оставляет blob ACTIVE без ссылок
навсегда: ORPHAN его не найдёт, сверка не сочтёт потерянным.

Потребители находятся интроспекцией, реестра в settings нет.
Обоснование — ARCHITECTURE.md, разделы 5.2 и 10.
"""

from django.db.models.signals import post_delete

DELETION_USER_ATTR = '_storage_deletion_user'
DELETION_CONSUMER_ATTR = '_storage_deletion_consumer'


def attribute_deletion(instance, *, user=None, consumer=''):
    """Пометить запись перед delete(), чтобы журнал знал, кто удалил файл.

        attribute_deletion(document, user=request.user, consumer='catalog.CatalogDocument')
        document.delete()

    Без пометки удаление пишется как каскадное. Звать detach() руками после
    delete() не нужно; он остаётся для снятия ссылки БЕЗ удаления строки.
    """
    setattr(instance, DELETION_USER_ATTR, user)
    setattr(instance, DELETION_CONSUMER_ATTR, consumer)
    return instance


def _detach_on_consumer_delete(sender, instance, field_name, **kwargs):
    from .models import FileObject
    from .services import StorageService

    file_object_id = getattr(instance, f'{field_name}_id', None)
    if file_object_id is None:
        return

    # Строки может уже не быть: в одном каскаде несколько записей могут
    # ссылаться на один и тот же FileObject (дедуплицированный файл), и
    # первый же обработчик его удалит. Для остальных работа сделана.
    file_object = FileObject.objects.filter(pk=file_object_id).first()
    if file_object is None:
        return

    consumer = getattr(instance, DELETION_CONSUMER_ATTR, '')
    if not consumer:
        consumer = f'{sender._meta.label}:cascade'

    StorageService.detach(
        file_object,
        user=getattr(instance, DELETION_USER_ATTR, None),
        consumer=consumer,
    )


def register_consumer_signals():
    """Подключает post_delete ко всем моделям с FK на FileObject. Вызывается
    из StorageConfig.ready(), когда все модели проекта уже загружены."""
    from .services import _iter_consumer_fields

    for model, field_name in _iter_consumer_fields():
        post_delete.connect(
            _make_receiver(field_name),
            sender=model,
            weak=False,
            dispatch_uid=f'storage.detach_on_delete.{model._meta.label}.{field_name}',
        )


def _make_receiver(field_name):
    def receiver(sender, instance, **kwargs):
        _detach_on_consumer_delete(sender, instance, field_name, **kwargs)

    return receiver
