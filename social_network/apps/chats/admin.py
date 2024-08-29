from django.contrib import admin
from .models import * #Портируем все модели


# class ChatAdmin(admin.ModelAdmin):
#     list_display = ('members',)  
# Register your models here.
class MessageAdmin(admin.ModelAdmin):
    list_display = ('chat', 'sender', 'content', 'timestamp' ) #Список полей которые мы видим в админке
    list_display_links = ('chat',) #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ( 'content',) # По каким полям можно делать поиск

    # list_filter = ( 'time_create',)# Добавляет фильтрацию по полям

# admin.site.register(Chat, ChatAdmin)

admin.site.register(Message, MessageAdmin) #Регистрируем приложение пост