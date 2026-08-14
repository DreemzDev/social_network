"""Живые обновления файлового менеджера (WebSocket).

Группа — на конкретную папку, а не одна общая: содержимое приватных папок
иначе рассылалось бы всем подключённым, и защита свелась бы к тому, что
клиент «не должен» его показывать. Доступ проверяется один раз при
подключении (storage/consumers.py).

Идентификатор места: exchange — '<owner_id>-<folder_id|0>', catalog и
deptdocs — '<folder_id|0>'. Разделитель не ':' — имя группы для channels
обязано быть ASCII без ':' и '/'.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

SCOPE_EXCHANGE = 'exchange'
SCOPE_CATALOG = 'catalog'
SCOPE_DEPTDOCS = 'deptdocs'

VALID_SCOPES = {SCOPE_EXCHANGE, SCOPE_CATALOG, SCOPE_DEPTDOCS}


def exchange_location(owner_id, folder_id=None) -> str:
    return f'{owner_id}-{folder_id or 0}'


def folder_location(folder_id=None) -> str:
    return str(folder_id or 0)


def group_name(scope: str, location: str) -> str:
    return f'fm.{scope}.{location}'


def broadcast(scope: str, location: str, *, action: str, actor=None, text: str = '') -> None:
    """Сказать смотрящим эту папку, что содержимое изменилось.

    Само изменение не передаётся: клиент перезапрашивает сетку (?partial=1)
    и вставляет готовый HTML. Иначе карточку с правами внутри пришлось бы
    рендерить второй раз в JS, и две копии прав обязаны были бы совпадать.

    actor — по нему клиент решает, показывать ли тост: своё действие
    пользователь уже видел.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:  # окружение без CHANNEL_LAYERS
        return

    try:
        async_to_sync(channel_layer.group_send)(
            group_name(scope, location),
            {
                'type': 'fm.event',
                'action': action,
                'actor_id': getattr(actor, 'pk', None),
                'actor_name': _display_name(actor),
                'text': text,
            },
        )
    except Exception:
        # Живое обновление — удобство, а не часть операции: упавший Redis не
        # должен превращать успешную загрузку в 500 и провоцировать повтор.
        logger.warning(
            'Не удалось разослать событие файлового менеджера %s/%s: %s',
            scope, location, action, exc_info=True,
        )


def _display_name(user) -> str:
    if user is None:
        return ''
    full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
    return full_name or getattr(user, 'username', '')
