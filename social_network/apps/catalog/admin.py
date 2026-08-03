from django.contrib import admin

from .models import CatalogFolder, CatalogDocument


@admin.register(CatalogFolder)
class CatalogFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'created_by', 'created_at')
    search_fields = ('name',)


@admin.register(CatalogDocument)
class CatalogDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'folder', 'file_object', 'uploaded_by', 'uploaded_at', 'is_deleted')
    list_filter = ('is_deleted',)
    search_fields = ('title',)
