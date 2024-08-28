from django.contrib import admin

from .models import * #Портируем все модели



class PhonebookAdmin(admin.ModelAdmin):
    list_display = ('id','title', 'book' ) #Список полей которые мы видим в админке
    list_display_links = ('id','title', 'book') #Поля на которые можно кликнуть и перейти на соответсвующую статью
   



admin.site.register(Phonebook, PhonebookAdmin) #Регистрируем приложение пост