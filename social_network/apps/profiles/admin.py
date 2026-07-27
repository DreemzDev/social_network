from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import * #Портируем все модели
# Register your models here.

class UserAdmin(admin.ModelAdmin):
    list_display = ('id','cat','last_activity', 'avatar', 'cover', 'employee_status', 'position', 'rank','last_name', 'first_name', 'patronymic', 'birthday', 'phone_pts','phone_city','phone_zs','phone_9','phone_hc' ) #Список полей которые мы видим в админке
    list_display_links = ('id','position', 'rank', 'last_name','first_name', 'patronymic') #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('last_name', 'position') # По каким полям можно делать поиск, запятая обязательно, т.к. нужно передовать картеж , если было бы два элемента запятая не нужна в конце



admin.site.register(User, UserAdmin) #Регистрируем приложение пост


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'event_type', 'user')
    list_filter = ('event_type',)
    search_fields = ('title',)


admin.site.register(Task)
admin.site.register(Note)