from django.conf import settings
from django.db import models

from storage.models import FileObject
from storage.utils import is_inline_safe


class Phonebook(models.Model):
    """Документ справочника — хранится через storage
    (STORAGE_CATEGORY_TTL['catalog'] = None, бессрочно), т.к. по смыслу это
    тот же информационный каталог (ARCHITECTURE.md, раздел 1.1).

    Подписи и мягкое удаление — как у CatalogDocument: справочник правит
    любой сотрудник, поэтому видно должно быть, кто именно."""

    title = models.CharField(max_length=255, verbose_name="Заголовок")
    file_object = models.ForeignKey(
        FileObject, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name="Справочник",
    )
    # Порядок в меню задаётся руками: по id справочники встают в порядке
    # добавления, а в меню организации у них свой смысл старшинства.
    sort_order = models.PositiveIntegerField(default=0, db_index=True, verbose_name='Порядок в меню')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    updated_at = models.DateTimeField(auto_now=True, null=True)

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    @property
    def is_previewable(self) -> bool:
        """Покажет ли браузер этот файл в <iframe>. Предикат storage, а не
        свой список: там объяснено, почему список именно белый."""
        return bool(self.file_object) and is_inline_safe(self.file_object.mime_type)

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse('phonebook', args=[self.pk])

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Справочники'
        verbose_name_plural = 'Справочники'
