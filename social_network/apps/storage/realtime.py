"""Живые обновления файлового менеджера (WebSocket).

Зачем группа на КОНКРЕТНУЮ папку, а не одна общая
--------------------------------------------------
У ленты постов (posts/realtime.py) группа одна на всех: все смотрят
примерно один и тот же список. Файловый менеджер устроен иначе — люди
одновременно сидят в разных папках трёх разных модулей, и событие «Иванов
переименовал файл» интересно только тем, кто сейчас смотрит именно ту
папку, где это произошло. Общая группа заставила бы каждого клиента
получать все события портала и самому решать, его ли это папка — лишний
трафик и лишний повод ошибиться.

Вторая причина важнее: у «Приватного доступа» содержимое папки закрыто
списком allowed_users. Общая группа означала бы рассылку имён файлов из
закрытых папок всем подключённым, и защита свелась бы к тому, что клиент
«не должен» их показывать. Группа на папку позволяет проверить доступ
ОДИН раз при подключении (storage/consumers.py) и дальше не рассылать
ничего лишнего.

Идентификатор места (location)
------------------------------
- exchange: '<owner_id>-<folder_id|0>' — личная папка сотрудника плюс
  подпапка внутри неё (0 = корень личной папки);
- catalog:  '<folder_id|0>' (0 = корень каталога);
- deptdocs: '<folder_id|0>' (0 = список доступных папок верхнего уровня).

Имя группы обязано быть валидным для channels (ASCII, без ':' и '/'),
поэтому разделитель — точка и дефис, а не двоеточие.
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
    """Сообщает всем, кто сейчас смотрит эту папку, что её содержимое
    изменилось.

    Намеренно НЕ передаёт содержимое изменения (имя файла, id, новую
    карточку). Клиент по этому событию перезапрашивает саму сетку
    (?partial=1) и подставляет готовый HTML — см. fm-actions.js,
    fmRefreshGrid(). Причина: карточка файла рендерится шестью разными
    include-шаблонами с правами внутри ({% if item.owner_id == ... %}), и
    собирать её второй раз в JS означало бы держать две копии одной
    разметки и прав, которые обязаны совпадать. Лишний GET на событие
    дешевле, чем расхождение прав между серверным и клиентским рендером.

    actor — кто инициировал; клиент по нему решает, показывать ли тост:
    своё собственное действие пользователь уже подтвердил локально.
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
        # Живое обновление — удобство, а не часть операции. Если брокер
        # (Redis) недоступен, файл всё равно уже загружен/переименован/
        # удалён, и превращать это в 500 нельзя: пользователь получил бы
        # ошибку на успешно выполненном действии, а повтор создал бы
        # дубликат. Соседи в этом случае просто увидят изменения при
        # следующем обновлении страницы — ровно как до появления WebSocket.
        logger.warning(
            'Не удалось разослать событие файлового менеджера %s/%s: %s',
            scope, location, action, exc_info=True,
        )


def _display_name(user) -> str:
    if user is None:
        return ''
    full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
    return full_name or getattr(user, 'username', '')
