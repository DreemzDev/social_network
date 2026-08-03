from django.contrib import admin

from .models import DepartmentDocument, DepartmentFolder


@admin.register(DepartmentFolder)
class DepartmentFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'created_by', 'created_at')
    search_fields = ('name',)
    filter_horizontal = ('allowed_users',)


@admin.register(DepartmentDocument)
class DepartmentDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'folder', 'file_object', 'uploaded_by', 'uploaded_at', 'is_deleted')
    list_filter = ('is_deleted',)
    search_fields = ('title',)
