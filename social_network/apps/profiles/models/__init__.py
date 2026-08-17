"""Модели profiles, разложенные по предметным областям.

В одном `models.py` их было двенадцать из четырёх несвязанных областей —
учётная запись, структура организации, расширения чата, личный органайзер и
уведомления. Тем же способом здесь уже разложены вьюхи (`profiles/views/`).

Отдельными приложениями области не стали намеренно: `User` — это
`AUTH_USER_MODEL`, он остаётся здесь в любом случае, а перенос остальных
моделей означал бы правку истории миграций у половины проекта (как с
`category`, см. SESSION_CONTEXT) ради выгоды, которой у внутреннего портала
нет — эти приложения никуда не переиспользуются.

Пакет реэкспортирует все имена, поэтому `from profiles.models import X`
снаружи работает как раньше. Порядок импорта — по зависимостям: структура
организации нужна `User`, а он — всем остальным.
"""
from .orgstructure import Category, PhoneType, Position, Rank, UserPhone
from .user import User, user_logged_in_callback, user_logged_out_callback
from .chat import MessageReaction, MessageReply
from .organizer import Event, Note, Task
from .notifications import Notification

__all__ = [
    'Category', 'PhoneType', 'Position', 'Rank', 'UserPhone',
    'User', 'user_logged_in_callback', 'user_logged_out_callback',
    'MessageReaction', 'MessageReply',
    'Event', 'Note', 'Task',
    'Notification',
]
