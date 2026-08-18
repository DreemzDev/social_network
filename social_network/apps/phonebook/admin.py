from django.contrib import admin

from .models import Phonebook


@admin.register(Phonebook)
class PhonebookAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'file_object', 'sort_order', 'is_deleted')
    list_display_links = ('id', 'title')
    list_editable = ('sort_order',)
    list_filter = ('is_deleted',)
    readonly_fields = ('created_by', 'created_at', 'updated_by', 'updated_at', 'deleted_by', 'deleted_at')
