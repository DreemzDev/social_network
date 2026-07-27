from django.contrib import admin

from .models import * #Портируем все модели



class PostImageInline(admin.TabularInline):
    model = PostImage
    extra = 0


class PostFileInline(admin.TabularInline):
    model = PostFile
    extra = 0


class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'time_create') #Список полей которые мы видим в админке
    list_display_links = ('id',) #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('title', 'content') # По каким полям можно делать поиск

    list_filter = ( 'time_create',)# Добавляет фильтрацию по полям
    inlines = [PostImageInline, PostFileInline]



admin.site.register(Post, PostAdmin) #Регистрируем приложение пост