from django.conf import settings
from django.db import models

from storage.models import FileObject


class ExchangeFile(models.Model):
    """Файл в папке сотрудника — точный аналог сетевой папки-обменника, где
    внутри лежат папки с фамилиями.

    Отдельной модели папки нет: папка — это и есть пользователь. Список папок
    равен списку сотрудников, поэтому новый сотрудник получает папку сам, без
    синхронизации и ручного создания.

    Видимость: содержимое всех папок видно всем сотрудникам, как в сетевой
    папке. Ограничение только на удаление — см. can_be_deleted_by().

    Срок жизни: файлы удаляются автоматически по TTL категории EXCHANGE
    (см. exchange.tasks.cleanup_expired_exchange_files).
    """

    file_object = models.ForeignKey(FileObject, on_delete=models.PROTECT, related_name='+')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exchange_files',
        verbose_name='Владелец папки',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+',
        verbose_name='Кто загрузил',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Корзина (ARCHITECTURE.md, раздел 6): удаление не трогает storage,
    # только помечает запись — восстановление отменяет пометку без каких-либо
    # обращений к StorageService.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Файл обменника'
        verbose_name_plural = 'Файлы обменника'

    def __str__(self):
        return f'{self.file_object.original_name} (папка {self.owner})'

    def can_be_deleted_by(self, user) -> bool:
        """Удалять может владелец папки (наводит порядок у себя) и тот, кто
        загрузил файл (ошибся папкой). Чужой файл в чужой папке — нет."""
        return self.owner_id == user.pk or self.uploaded_by_id == user.pk
