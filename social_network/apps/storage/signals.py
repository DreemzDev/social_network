"""Автоматический detach() при удалении записи-потребителя.

Зачем нужен отдельный механизм, если есть StorageService.detach()
-----------------------------------------------------------------
Чек-лист ARCHITECTURE.md (раздел 10) требует от потребителя вызвать detach()
после удаления своей записи, и для явного удаления этого достаточно. Но
запись потребителя удаляется не только явно: ForeignKey с on_delete=CASCADE
сносит её сам, и вызвать detach() в этом случае просто некому.

Реальные каскадные пути: CatalogFolder → CatalogDocument, DepartmentFolder →
DepartmentDocument, дерево папок через parent → self, User → ExchangeFile
(owner). Ни один из них не является нарушением дисциплины — их никто не
пишет, они срабатывают сами.

Последствие пропущенного detach() тихое и необратимое: blob остаётся ACTIVE
без единой ссылки. Механизм ORPHAN его не найдёт (статус не сменился),
purge_expired_orphans() не тронет, а find_untracked_files() не сочтёт
потерянным — запись FileBlob на месте, файл на диске тоже. Такой blob живёт
вечно, и ровно от этого модуль storage и создавался.

Поэтому detach() при удалении строки потребителя выполняется здесь —
автоматически, для всех потребителей сразу. Забыть его больше нельзя.

Атрибуция в журнале
-------------------
Сигнал не знает, кто инициировал удаление, а StorageAuditLog обязан отвечать
на вопрос «кто удалил файл» (ARCHITECTURE.md, раздел 2). Поэтому вызывающий
код помечает объект перед delete() через attribute_deletion() — сигнал
подхватывает пометку. Без пометки удаление записывается как каскадное:
пользователь неизвестен, consumer с суффиксом ':cascade'.

Модели-потребители находятся той же интроспекцией, что и в
_object_has_references() (ARCHITECTURE.md, раздел 5.2) — реестра в settings
нет, новый потребитель подключается автоматически.
"""

from django.db.models.signals import post_delete

DELETION_USER_ATTR = '_storage_deletion_user'
DELETION_CONSUMER_ATTR = '_storage_deletion_consumer'


def attribute_deletion(instance, *, user=None, consumer=''):
    """Помечает запись-потребитель перед её удалением, чтобы автоматический
    detach() записал в журнал, кто именно инициировал удаление.

    Пометка живёт только на экземпляре в памяти и нужна ровно до вызова
    instance.delete():

        attribute_deletion(document, user=request.user, consumer='catalog.CatalogDocument')
        document.delete()

    Вызывать detach() вручную после delete() не нужно — сигнал уже это
    сделал. StorageService.detach() остаётся публичным API для случая, когда
    ссылка снимается БЕЗ удаления строки: например, phonebook подменяет
    file_object на живой записи при замене файла справочника.
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
