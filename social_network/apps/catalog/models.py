from django.conf import settings
from django.db import models

from storage.models import FileObject


class CatalogFolder(models.Model):
    """Папка информационного каталога. Дерево через self-FK — плоской
    структуры недостаточно, документы группируются по разделам (например,
    'Кадры' → 'Приказы' → 'Приказы 2026')."""

    name = models.CharField(max_length=255, verbose_name='Название')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CatalogDocument(models.Model):
    """Документ в информационном каталоге — доступен всем аутентифицированным
    пользователям, без TTL (STORAGE_CATEGORY_TTL['catalog'] = None). Права
    здесь тривиальны (LoginRequiredMixin), поэтому этот модуль в первую
    очередь проверяет межмодульную дедупликацию файлов с обменником, а не
    сложную логику доступа."""

    folder = models.ForeignKey(
        CatalogFolder, on_delete=models.CASCADE, null=True, blank=True, related_name='documents'
    )
    file_object = models.ForeignKey(FileObject, on_delete=models.PROTECT, related_name='+')
    title = models.CharField(max_length=255, verbose_name='Название документа')

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title
