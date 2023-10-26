from django.contrib import admin

from .models import * #Портируем все модели

# Register your models here.
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name' ) #Список полей которые мы видим в админке
    list_display_links = ('id', 'name') #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('name',) # По каким полям можно делать поиск, запятая обязательно, т.к. нужно передовать картеж , если было бы два элемента запятая не нужна в конце



admin.site.register(Category, CategoryAdmin) #Регистрируем приложение пост