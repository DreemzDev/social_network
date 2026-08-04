from django.contrib import admin

from .models import ExchangeFile, ExchangeFolder


@admin.register(ExchangeFolder)
class ExchangeFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'parent', 'created_by', 'created_at')
    search_fields = ('name',)


@admin.register(ExchangeFile)
class ExchangeFileAdmin(admin.ModelAdmin):
    list_display = ('file_object', 'owner', 'folder', 'uploaded_by', 'uploaded_at', 'is_deleted')
    list_filter = ('is_deleted',)
    search_fields = ('file_object__original_name',)
