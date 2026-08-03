from django.contrib import admin

from .models import ExchangeFile


@admin.register(ExchangeFile)
class ExchangeFileAdmin(admin.ModelAdmin):
    list_display = ('file_object', 'owner', 'uploaded_by', 'uploaded_at', 'is_deleted')
    list_filter = ('is_deleted',)
    search_fields = ('file_object__original_name',)
