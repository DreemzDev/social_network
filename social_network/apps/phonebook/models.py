from django.conf import settings
from django.db import models

from storage.convert import is_convertible
from storage.models import FileObject
from storage.utils import is_inline_safe


class Phonebook(models.Model):
    """Документ справочника — хранится через storage
    (STORAGE_CATEGORY_TTL['catalog'] = None, бессрочно), т.к. по смыслу это
    тот же информационный каталог (ARCHITECTURE.md, раздел 1.1).

    Подписи и мягкое удаление — как у CatalogDocument: справочник правит
    любой сотрудник, поэтому видно должно быть, кто именно."""

    class Conversion(models.TextChoices):
        NONE = '', 'Не требуется'
        PENDING = 'pending', 'Готовится'
        DONE = 'done', 'Готово'
        FAILED = 'failed', 'Не удалось'

    title = models.CharField(max_length=255, verbose_name="Заголовок")
    file_object = models.ForeignKey(
        FileObject, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name="Справочник",
    )
    # Копия справочника в PDF: .docx и таблицы браузер в <iframe> не рисует,
    # и без неё половина справочников открывалась только скачиванием
    # (phonebook/tasks.py, ARCHITECTURE.md раздел 13). Отдельное поле, а не
    # подмена file_object — оригинал остаётся тем, что загрузили.
    pdf_file_object = models.ForeignKey(
        FileObject, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name="Версия для просмотра (PDF)",
    )
    conversion_status = models.CharField(
        max_length=10, choices=Conversion.choices, blank=True, default=Conversion.NONE,
    )
    conversion_error = models.CharField(max_length=200, blank=True)
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
    def preview_object(self):
        """Что показывать в <iframe>: PDF-копию, если она сделана, иначе сам
        файл — но только если браузер его нарисует."""
        if self.pdf_file_object_id:
            return self.pdf_file_object
        if self.file_object and is_inline_safe(self.file_object.mime_type):
            return self.file_object
        return None

    @property
    def is_previewable(self) -> bool:
        """Покажет ли браузер этот справочник в <iframe>. Предикат storage,
        а не свой список: там объяснено, почему список именно белый."""
        return self.preview_object is not None

    @property
    def needs_conversion(self) -> bool:
        """Файл не показывается в браузере, но его умеет открыть
        LibreOffice — значит, PDF-копию имеет смысл предложить."""
        return (
            self.file_object is not None
            and not self.is_previewable
            and is_convertible(self.file_object.original_name)
        )

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse('phonebook', args=[self.pk])

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Справочники'
        verbose_name_plural = 'Справочники'
