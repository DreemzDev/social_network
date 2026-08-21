class StorageError(Exception):
    """Базовое исключение модуля storage."""


class FileTooLargeError(StorageError):
    """Файл превышает предел размера загрузки (storage.limits)."""


class QuotaExceededError(StorageError):
    """Загрузка превысила бы квоту пользователя (storage.limits)."""


class InvalidArchiveError(StorageError):
    """Загруженный zip нельзя распаковать: не архив, повреждён, защищён
    паролем, пуст или упирается в пределы распаковки (см.
    storage/archives.py).

    Текст причины обязателен и показывается пользователю: «не удалось
    загрузить архив» без объяснения — это ровно тот молчаливый отказ,
    который запрещает правило 12.4.
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ArchiveTooLargeError(StorageError):
    """Запрошен архив больше пределов массового скачивания (storage.limits).

    В отличие от одиночного скачивания, zip собирается на лету и потому идёт
    через воркер Daphne, а не через nginx (X-Accel-Redirect на несуществующий
    файл не сошлёшь). Значит один запрос занимает воркер на всё время
    выгрузки — предел нужен, чтобы «скачать всё» не превращалось в
    самообслуживаемый отказ портала (ARCHITECTURE.md, раздел 8.1).
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message
