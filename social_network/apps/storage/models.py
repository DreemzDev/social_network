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


class StorageLimits(models.Model):
    """Единственная запись с пределами хранения, редактируемая в админке.

    Значения в человеческих единицах (МБ, дни, штуки) — их правит
    администратор портала, а не разработчик. Пока записи нет, действуют
    значения из settings.py (ARCHITECTURE.md, раздел 9).
    """

    max_upload_size_mb = models.PositiveIntegerField(
        default=100, verbose_name='Максимальный размер одного файла, МБ',
        help_text='Действует на любую загрузку: файл, вложение поста, файл внутри архива.',
    )
    user_quota_mb = models.PositiveIntegerField(
        null=True, blank=True, default=2048, verbose_name='Квота на сотрудника, МБ',
        help_text='Пусто — без квоты. Считается по уникальным файлам: копия чужого файла места не занимает.',
    )
    trash_retention_days = models.PositiveIntegerField(
        default=30, verbose_name='Сколько дней хранить в корзине',
    )
    orphan_retention_days = models.PositiveIntegerField(
        default=7, verbose_name='Сколько дней хранить файл без ссылок',
        help_text='После этого срока содержимое удаляется с диска окончательно.',
    )

    zip_max_files = models.PositiveIntegerField(
        default=200, verbose_name='Массовое скачивание: файлов в архиве',
    )
    zip_max_total_size_mb = models.PositiveIntegerField(
        default=1024, verbose_name='Массовое скачивание: общий объём, МБ',
    )

    archive_max_files = models.PositiveIntegerField(
        default=1000, verbose_name='Загрузка архива: файлов внутри',
    )
    archive_max_total_size_mb = models.PositiveIntegerField(
        default=2048, verbose_name='Загрузка архива: объём после распаковки, МБ',
    )
    archive_max_ratio = models.PositiveIntegerField(
        default=100, verbose_name='Загрузка архива: предел сжатия',
        help_text='Во сколько раз распакованное может быть больше архива. Выше — считается zip-бомбой.',
    )

    # Категории без автоочистки (каталог, документы отделов) здесь не
    # заводятся намеренно: срока хранения у них нет по смыслу, а поле в
    # админке обещало бы удаление, которого никто не выполняет.
    chat_ttl_days = models.PositiveIntegerField(
        null=True, blank=True, default=7, verbose_name='Вложения чата: срок хранения, дней',
        help_text='Пусто — хранить бессрочно.',
    )
    exchange_ttl_days = models.PositiveIntegerField(
        null=True, blank=True, default=7, verbose_name='Обменник: срок хранения, дней',
        help_text='Пусто — хранить бессрочно.',
    )

    class Meta:
        verbose_name = 'Пределы файлового хранилища'
        verbose_name_plural = 'Пределы файлового хранилища'

    def __str__(self):
        return 'Пределы файлового хранилища'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
