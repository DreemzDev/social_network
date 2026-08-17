from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import * #Портируем все модели
# Register your models here.

class UserPhoneInline(admin.TabularInline):
    """Телефоны — здесь же, на карточке сотрудника: пять отдельных полей
    ушли в справочник видов связи (PhoneType)."""

    model = UserPhone
    extra = 1


class UserAdmin(admin.ModelAdmin):
    list_display = ('id','cat','last_activity', 'avatar', 'cover', 'employee_status', 'position', 'rank','last_name', 'first_name', 'patronymic', 'birthday' ) #Список полей которые мы видим в админке
    list_display_links = ('id','position', 'rank', 'last_name','first_name', 'patronymic') #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('last_name', 'position__name') # По каким полям можно делать поиск, запятая обязательно, т.к. нужно передовать картеж , если было бы два элемента запятая не нужна в конце
    list_filter = ('cat', 'position', 'rank')
    list_select_related = ('cat', 'position', 'rank')
    inlines = [UserPhoneInline]


admin.site.register(User, UserAdmin) #Регистрируем приложение пост


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    """Структура организации: кто кому подчиняется и кто может назначить
    себе должность сам.

    Отступ в списке показывает уровень подчинения — плоский перечень
    должностей структуру не передаёт, а ради разового взгляда на неё
    разворачивать настоящее дерево незачем (тот же приём, что у выбора
    папки в файловом менеджере)."""

    list_display = ('indented_name', 'department', 'parent', 'holders_count',
                    'max_holders', 'assignable_by_user', 'order')
    list_editable = ('assignable_by_user', 'order')
    list_filter = ('department', 'assignable_by_user')
    search_fields = ('name',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('parent', 'department').annotate(
            _holders_count=Count('holders'),
        )

    @admin.display(description='Должность')
    def indented_name(self, obj):
        return format_html('{}{}', mark_safe('&nbsp;' * 4 * obj.depth), obj.name)

    @admin.display(description='Занимают', ordering='_holders_count')
    def holders_count(self, obj):
        return obj._holders_count


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    list_display_links = ('id', 'name')
    search_fields = ('name',)


@admin.register(Rank)
class RankAdmin(admin.ModelAdmin):
    list_display = ('name', 'order')
    list_editable = ('order',)
    search_fields = ('name',)


@admin.register(PhoneType)
class PhoneTypeAdmin(admin.ModelAdmin):
    """Виды связи. Маска ввода живёт здесь же — она свойство вида номера, а
    не разметки: раньше пять масок были прописаны в base.html."""

    list_display = ('name', 'mask', 'order')
    list_editable = ('mask', 'order')
    search_fields = ('name',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'event_type', 'user')
    list_filter = ('event_type',)
    search_fields = ('title',)


admin.site.register(Task)
admin.site.register(Note)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('text', 'recipient', 'kind', 'is_read', 'created')
    list_filter = ('kind', 'is_read')
    search_fields = ('text',)