from django.contrib import admin
from .models import *
# Register your models here.
class GalleryAdmin(admin.ModelAdmin):
    list_display = ('id','uploaded_at', 'image' ) #Список полей которые мы видим в админке
    list_display_links = ('id', 'image') #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('id',) # По каким полям можно делать поиск

    list_filter = ( 'uploaded_at',)# Добавляет фильтрацию по полям



admin.site.register(GalleryImage, GalleryAdmin) #Регистрируем приложение пост