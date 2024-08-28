from django.contrib import admin

from .models import * #Портируем все модели



class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'time_create', 'photo' ) #Список полей которые мы видим в админке
    list_display_links = ('id',) #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('title', 'content') # По каким полям можно делать поиск

    list_filter = ( 'time_create',)# Добавляет фильтрацию по полям



admin.site.register(Post, PostAdmin) #Регистрируем приложение пост