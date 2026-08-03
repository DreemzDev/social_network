import os

from django.conf import settings
from django.db import models


def blob_upload_path(instance, filename):
    checksum = instance.checksum
    return os.path.join('storage', 'blobs', checksum[:2], checksum)


class FileBlob(models.Model):
    """Физическое содержимое файла. Одно содержимое — одна запись, одна копия
    на диске (checksum уникален). Модули-потребители никогда не ссылаются
    сюда напрямую — только через FileObject (см. ARCHITECTURE.md, раздел 2)."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активен'
        ORPHAN = 'orphan', 'Без ссылок (ожидает удаления)'

    file = models.FileField(upload_to=blob_upload_path)
    checksum = models.CharField(max_length=64, unique=True)
    size = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    orphaned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'orphaned_at'])]

    def __str__(self):
        return f'{self.checksum[:12]} ({self.size} байт)'


class FileObject(models.Model):
    """Именованный объект файла — то, на что ссылаются модули-потребители.
    Одно и то же содержимое (FileBlob) может иметь несколько FileObject с
    разными именами, категориями и правами (права — на стороне потребителя,
    см. ARCHITECTURE.md раздел 8)."""

    class Category(models.TextChoices):
        CHAT = 'chat', 'Вложение чата'
        EXCHANGE = 'exchange', 'Обменник'
        DOCUMENT = 'document', 'Документ подразделения'
        CATALOG = 'catalog', 'Информационный каталог'
        TASK = 'task', 'Файл задачи'

    blob = models.ForeignKey(FileBlob, on_delete=models.PROTECT, related_name='file_objects')
    original_name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def size(self):
        return self.blob.size

    @property
    def mime_type(self):
        return self.blob.mime_type

    @property
    def extension(self):
        _, dot, ext = self.original_name.rpartition('.')
        return ext.upper() if dot else '—'

    def __str__(self):
        return self.original_name


class StorageAuditLog(models.Model):
    """Журнал операций над файлами. checksum хранится строкой, а не FK на
    FileBlob — записи должны переживать физическое удаление blob'а."""

    class Action(models.TextChoices):
        UPLOAD = 'upload', 'Загрузка'
        DETACH = 'detach', 'Отвязка ссылки'
        PURGE = 'purge', 'Физическое удаление'
        RESTORE = 'restore', 'Восстановление из ORPHAN'

    action = models.CharField(max_length=20, choices=Action.choices)
    checksum = models.CharField(max_length=64, db_index=True)
    original_name = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    consumer = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_action_display()} {self.checksum[:12]} ({self.created_at:%Y-%m-%d %H:%M})'
