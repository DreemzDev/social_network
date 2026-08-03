from django.contrib import admin

from .models import * #Портируем все модели



class PhonebookAdmin(admin.ModelAdmin):
    list_display = ('id','title', 'file_object' ) #Список полей которые мы видим в админке
    list_display_links = ('id','title', 'file_object') #Поля на которые можно кликнуть и перейти на соответсвующую статью
   



admin.site.register(Phonebook, PhonebookAdmin) #Регистрируем приложение пост