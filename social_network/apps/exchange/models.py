from django.conf import settings
from django.db import models

from storage.models import FileObject


class ExchangeFolder(models.Model):
    """Подпапка внутри личной папки сотрудника — например, «Проект Альфа»
    внутри папки Иванова. Не самостоятельная единица: у каждой подпапки
    обязательно есть owner — чья это личная папка, в которой она заведена.
    Дерево — тем же паттерном self-FK, что и CatalogFolder/DepartmentFolder.

    Корень личной папки подпапкой не является (ExchangeFile.folder=None —
    это и есть корень), поэтому у ExchangeFolder нет отдельной модели
    "корневая папка сотрудника": список корневых папок по-прежнему равен
    списку сотрудников (ARCHITECTURE.md, раздел 1.1).

    Права на подпапки такие же открытые, как и на файлы (см.
    ExchangeFile.can_be_deleted_by): обменник — сетевая папка, видимая и
    доступная для записи всем, а не приватное пространство владельца.
    Создать подпапку в чужой личной папке может кто угодно — так же, как
    положить туда файл."""

    name = models.CharField(max_length=255, verbose_name='Название')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exchange_folders',
        verbose_name='Чья личная папка',
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Подпапка обменника'
        verbose_name_plural = 'Подпапки обменника'

    def __str__(self):
        return f'{self.name} (папка {self.owner})'

    def get_absolute_url(self) -> str:
        """Адрес подпапки. Живёт на модели, а не собирается в шаблоне,
        потому что для ссылки нужны ДВА идентификатора — владелец личной
        папки и сама подпапка, — и общая карточка папки
        (includes/fm_folder_card.html) второго из них не знает: у каталога
        и приватного доступа владельца в URL нет."""
        from django.urls import reverse

        return reverse('exchange_subfolder', args=[self.owner_id, self.pk])

    def get_download_url(self) -> str:
        """Скачать папку целиком архивом. Рядом с get_delete_url по той же
        причине: карточка папки общая на три модуля и адреса берёт с
        модели, а не собирает {% url %} с именем маршрута каждого."""
        from django.urls import reverse

        return reverse('exchange_folder_download', args=[self.pk])

    def get_delete_url(self) -> str:
        from django.urls import reverse

        return reverse('exchange_folder_delete', args=[self.pk])

    def can_be_deleted_by(self, user) -> bool:
        """Удалять может владелец личной папки (наводит порядок у себя) и
        тот, кто создал подпапку (ошибся папкой) — тот же принцип, что и
        для файлов в ExchangeFile.can_be_deleted_by."""
        return self.owner_id == user.pk or self.created_by_id == user.pk


class ExchangeFile(models.Model):
    """Файл в папке сотрудника — точный аналог сетевой папки-обменника, где
    внутри лежат папки с фамилиями.

    Отдельной модели корневой папки нет: корень — это и есть пользователь.
    Список корневых папок равен списку сотрудников, поэтому новый сотрудник
    получает папку сам, без синхронизации и ручного создания. Внутри личной
    папки могут быть подпапки (ExchangeFolder) — folder=None означает файл
    прямо в корне личной папки владельца.

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
    folder = models.ForeignKey(
        ExchangeFolder, on_delete=models.CASCADE, null=True, blank=True, related_name='files',
        verbose_name='Подпапка',
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
