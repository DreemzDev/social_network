from django.contrib import admin


from .models import * #Портируем все модели

# Register your models here.
class CommentAdmin(admin.ModelAdmin):
    list_display = ('post', 'comment_author', 'comment_text', 'comment_pubdate' ) #Список полей которые мы видим в админке
    list_display_links = ('comment_author', 'comment_text') #Поля на которые можно кликнуть и перейти на соответсвующую статью
    search_fields = ('comment_author',) # По каким полям можно делать поиск

    list_filter = ( 'comment_pubdate',)# Добавляет фильтрацию по полям


admin.site.register(Comment, CommentAdmin)