from django.db import models

from storage.models import FileObject


class Phonebook(models.Model):
    """Документ справочника — хранится через storage
    (STORAGE_CATEGORY_TTL['catalog'] = None, бессрочно), т.к. по смыслу это
    тот же информационный каталог (ARCHITECTURE.md, раздел 1.1)."""

    title = models.CharField(max_length=255, verbose_name="Заголовок")
    file_object = models.ForeignKey(
        FileObject, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name="Справочник",
    )

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = 'Справочники'
        verbose_name_plural = 'Справочники'