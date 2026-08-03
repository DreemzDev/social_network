class StorageError(Exception):
    """Базовое исключение модуля storage."""


class FileTooLargeError(StorageError):
    """Файл превышает STORAGE_MAX_UPLOAD_SIZE."""


class QuotaExceededError(StorageError):
    """Загрузка превысила бы квоту пользователя (STORAGE_USER_QUOTA)."""
