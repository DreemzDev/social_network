from django.urls import path

from .views import *

urlpatterns = [
    path('addprofile/', AddProfile.as_view(), name='addprofile'),
    path('settingprofile/', SettingProfile.as_view(), name='settingprofile'),
    path('users/', ShowUsers.as_view(), name='show_users'),
    path('phones/', ShowPhones.as_view(), name='show_phones'),
    # Те же два списка, суженные до подразделения. Прежние вьюхи приложения
    # category были их обеднёнными копиями: без пагинации, без dept_links и
    # без prefetch телефонов.
    path('filterUsers/<int:cat_id>/', ShowUsers.as_view(), name='filterUsers'),
    path('filterPhones/<int:cat_id>/', ShowPhones.as_view(), name='filterPhones'),
    path('structure/', OrgStructureView.as_view(), name='org_structure'),
# Список всех диалогов
    path('dialogs/', DialogsListView.as_view(), name='dialogs_list'),
    
    # Конкретный диалог с пользователем
    path('dialog/<int:user_id>/', DialogMessagesView.as_view(), name='dialog_messages'),
    
    # Отправка сообщения (HTTP-путь; используется для reply, но обрабатывает и обычные сообщения)
    path('send-message-reply/<int:user_id>/', SendMessageWithReplyView.as_view(), name='send_message_reply'),
    
    # API для ленивой загрузки сообщений
    path('load-messages/<int:user_id>/', LoadMoreMessagesView.as_view(), name='load_messages'),
    
    # API для получения количества непрочитанных
    path('get-unread-count/', GetUnreadCountView.as_view(), name='get_unread_count'),
    
    # API для пометки сообщений как прочитанных
    path('mark-messages-read/<int:user_id>/', MarkMessagesReadView.as_view(), name='mark_messages_read'),

    # Редактирование и удаление своих сообщений
    path('edit-message/<int:message_id>/', EditMessageView.as_view(), name='edit_message'),
    path('delete-message/<int:message_id>/', DeleteMessageView.as_view(), name='delete_message'),

    # Реакции на сообщения
    path('toggle-reaction/<int:message_id>/', ToggleMessageReactionView.as_view(), name='toggle_reaction'),

    # Личные задачи
    path('task/list/', TaskListFeedView.as_view(), name='task_list_feed'),
    path('task/create/', TaskCreateView.as_view(), name='task_create'),
    path('task/quick-create/', TaskQuickCreateView.as_view(), name='task_quick_create'),
    path('task/<int:task_id>/toggle/', TaskToggleView.as_view(), name='task_toggle'),
    path('task/<int:task_id>/status/', TaskStatusUpdateView.as_view(), name='task_status_update'),
    path('task/<int:task_id>/delete/', TaskDeleteView.as_view(), name='task_delete'),
    path('task/<int:task_id>/edit/', TaskEditView.as_view(), name='task_edit'),

    # Уведомления
    path('notification/<int:notification_id>/read/', NotificationMarkReadView.as_view(), name='notification_mark_read'),
    path('notification/mark-all-read/', NotificationMarkAllReadView.as_view(), name='notification_mark_all_read'),

    # Личные заметки (стикеры)
    path('note/create/', NoteCreateView.as_view(), name='note_create'),
    path('note/<int:note_id>/edit/', NoteEditView.as_view(), name='note_edit'),
    path('note/<int:note_id>/delete/', NoteDeleteView.as_view(), name='note_delete'),

    # Календарь
    path('event/create/', EventCreateView.as_view(), name='event_create'),
    path('event/<int:event_id>/delete/', EventDeleteView.as_view(), name='event_delete'),
    path('calendar/users/', CalendarUsersListView.as_view(), name='calendar_users_list'),
    path('calendar/task-create/', CalendarTaskCreateView.as_view(), name='calendar_task_create'),
    path('calendar/events/', CalendarEventsFeedView.as_view(), name='calendar_events_feed'),

    # Статус сотрудника
    path('employee-status/update/', EmployeeStatusUpdateView.as_view(), name='employee_status_update'),
]