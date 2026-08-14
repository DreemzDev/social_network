"""Уборка файлов обычных `ImageField`/`FileField` — тех, что мимо `FileBlob`.

Django файлы за удалённой записью не удаляет, и на боевой БД так набралось
58 файлов (~43 МБ) при нуле ссылок. Здесь это исправляется, но через
`transaction.on_commit`: откат вернёт запись, а файл вернуть неоткуда.

`FileBlob.file` регистрировать **нельзя** — на один файл ссылается несколько
`FileObject`, и удаление по одной записи снесло бы файл из-под живых ссылок.
Поэтому регистрация явная, по конкретным полям, из `AppConfig.ready()`::

    register_file_cleanup(self.get_model('GalleryImage'), 'image')

Подробности — SESSION_CONTEXT.md, «Картинки вне storage».
"""
import logging
import os

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, pre_save

logger = logging.getLogger(__name__)

# {модель: (имена полей, ...)} — заполняется регистрацией из AppConfig.ready().
# Нужен не только сигналам: по нему же команда media_verify понимает, какие
# каталоги в MEDIA_ROOT кому принадлежат и что в них считать мусором.
_REGISTRY = {}


def register_file_cleanup(model, *field_names):
    """Удалять файлы этих полей при удалении записи и при замене значения.

    Вызывать из `AppConfig.ready()`: к этому моменту модели загружены, а
    повторный вызов безопасен — сигналы подключаются с `dispatch_uid`.
    """
    if not field_names:
        raise ValueError('Укажите хотя бы одно поле файла')

    _REGISTRY[model] = tuple(field_names)

    label = f'{model._meta.label}.mediafiles'
    post_delete.connect(_delete_files, sender=model, dispatch_uid=f'{label}.delete')
    pre_save.connect(_delete_replaced_file, sender=model, dispatch_uid=f'{label}.replace')


def registered_fields() -> dict:
    """{модель: (поля, ...)} — всё, за чем следит модуль."""
    return dict(_REGISTRY)


def _delete_files(sender, instance, **kwargs):
    """Запись удалена — файлы больше никому не нужны.

    Работает и на каскаде: у постов удаление `Post` уносит `PostImage`
    через FK, и сигнал приходит на каждую удалённую строку.
    """
    for field_name in _REGISTRY[sender]:
        _delete_on_commit(getattr(instance, field_name))


def _delete_replaced_file(sender, instance, update_fields=None, **kwargs):
    """Файл заменили другим — прежний остался бы на диске навсегда.

    Проверка `update_fields` обязательна: `UserActivityMiddleware` зовёт
    `save(update_fields=['last_activity'])` на каждый запрос, и без неё
    каждый такой вызов делал бы лишний SELECT.
    """
    if instance.pk is None:  # создание — заменять нечего
        return

    field_names = _REGISTRY[sender]
    if update_fields is not None:
        field_names = [name for name in field_names if name in update_fields]
        if not field_names:
            return

    try:
        previous = sender._default_manager.get(pk=instance.pk)
    except sender.DoesNotExist:  # pk задан вручную, записи ещё нет
        return

    for field_name in field_names:
        old_file = getattr(previous, field_name)
        if old_file and old_file.name != getattr(instance, field_name).name:
            _delete_on_commit(old_file)


def _delete_on_commit(field_file):
    """Удалить файл после фиксации транзакции.

    Лишний файл на диске чинится `media_verify`, потерянный из-под живой
    записи — ничем. Поэтому и ошибка удаления операцию не роняет.
    """
    if not field_file or not field_file.name:
        return

    storage, name = field_file.storage, field_file.name

    def remove():
        try:
            storage.delete(name)
        except Exception:
            logger.warning('Не удалось удалить файл %s', name, exc_info=True)

    transaction.on_commit(remove)


def upload_roots() -> dict:
    """{каталог в MEDIA_ROOT: [(модель, поле), ...]} — куда пишут поля.

    `upload_to` бывает с датой в пути (`photos/%Y/%m/%d/`), поэтому корнем
    считается часть до первого `%`. Поля с `upload_to`-функцией (так устроен
    `FileBlob.file`) сюда не попадают: по функции нельзя узнать, какие
    каталоги она порождает, — да они здесь и не нужны, регистрируются только
    простые ImageField.
    """
    roots = {}
    for model, field_names in _REGISTRY.items():
        for field_name in field_names:
            upload_to = model._meta.get_field(field_name).upload_to
            if not isinstance(upload_to, str) or not upload_to:
                continue
            root = upload_to.split('%')[0].strip('/')
            if root:
                roots.setdefault(root, []).append((model, field_name))
    return roots


def find_untracked_media() -> list:
    """Файлы в каталогах зарегистрированных полей, на которые нет ссылки.

    Только эти каталоги: `media/storage/` сверяет `storage_verify`, а
    доstorage'ное наследство команда не видит и удалить не может.

    Ссылки собираются по всем полям сразу — каталоги вложены друг в друга
    (`gallery/thumbs/` внутри `gallery/`), и при сверке по каждому отдельно
    живые миниатюры попадали бы в мусор.
    """
    media_root = str(settings.MEDIA_ROOT)

    referenced = set()
    for model, field_names in _REGISTRY.items():
        for field_name in field_names:
            referenced.update(
                # В БД имя лежит с прямыми слэшами всегда, на диске — с
                # os.sep: на Windows без нормализации ни одна ссылка не
                # совпала бы, и команда предложила бы удалить все файлы.
                name.replace('\\', '/')
                for name in model._default_manager.order_by()
                .values_list(field_name, flat=True).distinct() if name
            )

    untracked = set()
    for root in upload_roots():
        directory = os.path.join(media_root, root)
        if not os.path.isdir(directory):
            continue

        for current_dir, _, file_names in os.walk(directory):
            for file_name in file_names:
                path = os.path.join(current_dir, file_name)
                relative = os.path.relpath(path, media_root).replace(os.sep, '/')
                if relative not in referenced:
                    untracked.add(path)

    return sorted(untracked)
