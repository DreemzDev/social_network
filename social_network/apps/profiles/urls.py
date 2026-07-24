from django.urls import path

from .views import *

urlpatterns = [
    path('addprofile/<int:user_id>/', AddProfile.as_view(), name='addprofile'), 
    path('settingprofile/<int:user_id>/', SettingProfile.as_view(), name='settingprofile'),
    path('users/', ShowUsers.as_view(), name='show_users'),
    path('phones/', ShowPhones.as_view(), name='show_phones'),
# Список всех диалогов
    path('dialogs/', DialogsListView.as_view(), name='dialogs_list'),
    
    # Конкретный диалог с пользователем
    path('dialog/<int:user_id>/', DialogMessagesView.as_view(), name='dialog_messages'),
    
    # Отправка сообщения
    path('send-message/<int:user_id>/', SendMessageView.as_view(), name='send_message'),
    
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
]