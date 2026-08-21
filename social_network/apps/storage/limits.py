"""Пределы хранения: запись из админки, а если её нет — значения settings.py.

Числа вроде «100 МБ на файл» меняет администратор портала, а не
разработчик, поэтому settings.py здесь — только значения по умолчанию
(ARCHITECTURE.md, раздел 9). Все обращения к пределам идут через этот
модуль: значение читается при каждом вызове, иначе правка в админке
подействовала бы только после перезапуска Daphne.
"""
from django.conf import settings
from django.db.utils import DatabaseError

MB = 1024 * 1024


def _row():
    """Запись пределов или None, если её ещё не заводили.

    Кеша намеренно нет: смысл настройки в админке — подействовать сразу, а
    LocMemCache живёт в одном процессе из нескольких. Запрос идёт по
    первичному ключу и на фоне самой загрузки файла незаметен.
    """
    from .models import StorageLimits

    try:
        return StorageLimits.objects.filter(pk=1).first()
    except DatabaseError:
        # Таблицы ещё нет (первый migrate на чистой БД) — пределы нужны
        # раньше, чем она появится, только у management-команд.
        return None


def _limit(field, settings_name, default, *, factor=1):
    row = _row()
    if row is None:
        return getattr(settings, settings_name, default)

    value = getattr(row, field)
    # None у nullable-поля — это «без ограничения», а не «взять из settings»:
    # иначе очистить квоту в админке было бы нельзя.
    return None if value is None else value * factor


def max_upload_size() -> int:
    return _limit('max_upload_size_mb', 'STORAGE_MAX_UPLOAD_SIZE', 100 * MB, factor=MB)


def user_quota():
    return _limit('user_quota_mb', 'STORAGE_USER_QUOTA', None, factor=MB)


def trash_retention_days() -> int:
    return _limit('trash_retention_days', 'STORAGE_TRASH_RETENTION_DAYS', 30)


def orphan_retention_days() -> int:
    return _limit('orphan_retention_days', 'STORAGE_ORPHAN_RETENTION_DAYS', 7)


def zip_max_files() -> int:
    return _limit('zip_max_files', 'STORAGE_ZIP_MAX_FILES', 200)


def zip_max_total_size() -> int:
    return _limit('zip_max_total_size_mb', 'STORAGE_ZIP_MAX_TOTAL_SIZE', 1024 * MB, factor=MB)


def archive_max_files() -> int:
    return _limit('archive_max_files', 'STORAGE_ARCHIVE_MAX_FILES', 1000)


def archive_max_total_size() -> int:
    return _limit('archive_max_total_size_mb', 'STORAGE_ARCHIVE_MAX_TOTAL_SIZE', 2048 * MB, factor=MB)


def archive_max_ratio() -> int:
    return _limit('archive_max_ratio', 'STORAGE_ARCHIVE_MAX_RATIO', 100)


# Поле записи для тех категорий, срок хранения которых правится в админке.
# Остальные (каталог, документы отделов) бессрочны по смыслу и берутся из
# settings.STORAGE_CATEGORY_TTL.
_TTL_FIELDS = {'chat': 'chat_ttl_days', 'exchange': 'exchange_ttl_days'}


def category_ttl_days(category):
    """Срок хранения категории в днях, None — бессрочно."""
    field = _TTL_FIELDS.get(str(category))
    row = _row() if field else None
    if row is not None:
        return getattr(row, field)
    return getattr(settings, 'STORAGE_CATEGORY_TTL', {}).get(category)
