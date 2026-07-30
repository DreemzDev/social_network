"""
views.py вырос слишком большим (1000+ строк, несколько несвязанных доменов),
поэтому разбит на модули по смыслу. Пакет реэкспортирует все имена, чтобы
`from .views import *` в urls.py и `from .views import clear_user_cache`
в signals.py продолжали работать без изменений.
"""
from ._common import clear_user_cache, push_chat_event, notify

from .profile import (
    AddProfile, SettingProfile, ShowUsers, ShowPhones, EmployeeStatusUpdateView,
)

from .chat import (
    DialogsWithUnreadMixin, DialogsListView, DialogMessagesView, LoadMoreMessagesView,
    GetUnreadCountView, SendMessageWithReplyView, MarkMessagesReadView, EditMessageView,
    DeleteMessageView, ToggleMessageReactionView, ALLOWED_REACTION_EMOJIS,
)

from .tasks import (
    TaskListFeedView, TaskCreateView, TaskQuickCreateView, TaskStatusUpdateView,
    TaskToggleView, TaskDeleteView, TaskEditView,
)

from .notes import NoteCreateView, NoteEditView, NoteDeleteView

from .calendar import (
    EventCreateView, EventDeleteView, CalendarUsersListView, CalendarTaskCreateView,
    CalendarEventsFeedView, CALENDAR_COLORS,
)

from .notifications import NotificationMarkReadView, NotificationMarkAllReadView

__all__ = [
    'clear_user_cache', 'push_chat_event', 'notify',
    'AddProfile', 'SettingProfile', 'ShowUsers', 'ShowPhones', 'EmployeeStatusUpdateView',
    'DialogsWithUnreadMixin', 'DialogsListView', 'DialogMessagesView', 'LoadMoreMessagesView',
    'GetUnreadCountView', 'SendMessageWithReplyView', 'MarkMessagesReadView', 'EditMessageView',
    'DeleteMessageView', 'ToggleMessageReactionView', 'ALLOWED_REACTION_EMOJIS',
    'TaskListFeedView', 'TaskCreateView', 'TaskQuickCreateView', 'TaskStatusUpdateView',
    'TaskToggleView', 'TaskDeleteView', 'TaskEditView',
    'NoteCreateView', 'NoteEditView', 'NoteDeleteView',
    'EventCreateView', 'EventDeleteView', 'CalendarUsersListView', 'CalendarTaskCreateView',
    'CalendarEventsFeedView', 'CALENDAR_COLORS',
    'NotificationMarkReadView', 'NotificationMarkAllReadView',
]
