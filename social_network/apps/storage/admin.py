from django.contrib import admin

from .models import FileBlob, FileObject, StorageAuditLog, StorageLimits


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


@admin.register(StorageLimits)
class StorageLimitsAdmin(admin.ModelAdmin):
    """Пределы хранения — одна запись на портал, поэтому список сразу
    открывает её саму (как SiteSettings)."""

    fieldsets = (
        ('Загрузка', {'fields': ('max_upload_size_mb', 'user_quota_mb')}),
        ('Сроки хранения', {
            'fields': ('trash_retention_days', 'orphan_retention_days',
                       'chat_ttl_days', 'exchange_ttl_days'),
        }),
        ('Скачивание архивом', {'fields': ('zip_max_files', 'zip_max_total_size_mb')}),
        ('Загрузка архива с распаковкой', {
            'fields': ('archive_max_files', 'archive_max_total_size_mb', 'archive_max_ratio'),
        }),
    )

    def has_add_permission(self, request):
        return not StorageLimits.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse

        obj = StorageLimits.load()
        return redirect(reverse('admin:storage_storagelimits_change', args=[obj.pk]))
