from django.contrib import admin

from .models import FileBlob, FileObject, StorageAuditLog


@admin.register(FileBlob)
class FileBlobAdmin(admin.ModelAdmin):
    list_display = ('checksum', 'size', 'mime_type', 'status', 'orphaned_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('checksum',)
    readonly_fields = ('checksum', 'size', 'mime_type', 'created_at')


@admin.register(FileObject)
class FileObjectAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'category', 'blob', 'uploaded_by', 'uploaded_at')
    list_filter = ('category',)
    search_fields = ('original_name',)
    readonly_fields = ('uploaded_at',)


@admin.register(StorageAuditLog)
class StorageAuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'checksum', 'original_name', 'consumer', 'user', 'created_at')
    list_filter = ('action',)
    search_fields = ('checksum', 'original_name')
    readonly_fields = [f.name for f in StorageAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
