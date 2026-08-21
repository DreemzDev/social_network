"""Конвертация офисных документов в PDF внешним LibreOffice.

Нужна затем, что браузер показывает в <iframe> только PDF и картинки: .docx
справочника пользователь до этого мог лишь скачать и открыть в редакторе.
Питоновской библиотеки, которая верстала бы .docx с приемлемым результатом,
нет — берётся тот же LibreOffice, что уже стоит на Астре по умолчанию.
Обоснование выбора — ARCHITECTURE.md, раздел 13.
"""
import os
import shutil
import subprocess
import tempfile

from django.conf import settings

# Форматы, которые LibreOffice берёт на вход и которые встречаются в
# портале. Список белый: soffice умеет больше, но обещать пользователю
# кнопку «показать в браузере» стоит только для предсказуемого результата.
CONVERTIBLE_EXTENSIONS = frozenset({
    'DOC', 'DOCX', 'ODT', 'RTF',
    'XLS', 'XLSX', 'ODS',
    'PPT', 'PPTX', 'ODP',
})

# Больше двух минут документ конвертируется только если soffice завис:
# обычный справочник на сотню страниц укладывается в 5-15 секунд.
CONVERT_TIMEOUT = 120

# Где искать soffice, если STORAGE_SOFFICE_PATH не задан. Первый путь —
# типичная установка на Астре и других Linux, остальные — Windows-машина
# разработчика.
_KNOWN_PATHS = (
    '/usr/bin/soffice',
    '/usr/bin/libreoffice',
    '/opt/libreoffice/program/soffice',
    r'C:\Program Files\LibreOffice\program\soffice.exe',
    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
)


class ConversionError(Exception):
    """Документ не удалось привести к PDF."""


def soffice_path():
    """Путь к LibreOffice или None, если его на машине нет."""
    configured = getattr(settings, 'STORAGE_SOFFICE_PATH', None)
    if configured:
        return configured if os.path.exists(configured) else None

    found = shutil.which('soffice') or shutil.which('libreoffice')
    if found:
        return found

    return next((path for path in _KNOWN_PATHS if os.path.exists(path)), None)


def converter_available() -> bool:
    return soffice_path() is not None


def is_convertible(original_name: str) -> bool:
    _, dot, extension = (original_name or '').rpartition('.')
    return bool(dot) and extension.upper() in CONVERTIBLE_EXTENSIONS


def convert_to_pdf(source_path: str, original_name: str, target_dir: str) -> str:
    """Делает из документа PDF и возвращает путь к нему в target_dir.

    Исходник копируется во временный файл С РАСШИРЕНИЕМ: на диске blob
    называется контрольной суммой без расширения, а фильтр LibreOffice
    выбирает по расширению — без него soffice отказывается работать с
    файлом, тип которого «не распознан».
    """
    executable = soffice_path()
    if executable is None:
        raise ConversionError('LibreOffice на сервере не установлен')

    _, _, extension = original_name.rpartition('.')
    work_dir = tempfile.mkdtemp(dir=target_dir)
    source_copy = os.path.join(work_dir, f'source.{extension.lower()}')
    shutil.copyfile(source_path, source_copy)

    # Свой профиль на каждый запуск: с общим профилем второй soffice не
    # запускается, а молча передаёт работу первому и завершается с кодом 0 —
    # задача отчиталась бы об успехе, не создав PDF.
    profile_dir = os.path.join(work_dir, 'profile')
    command = [
        executable,
        f'-env:UserInstallation=file:///{profile_dir.replace(os.sep, "/").lstrip("/")}',
        '--headless', '--norestore', '--nolockcheck', '--nodefault',
        '--convert-to', 'pdf',
        '--outdir', work_dir,
        source_copy,
    ]

    try:
        result = subprocess.run(
            command, capture_output=True, timeout=CONVERT_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ConversionError('LibreOffice не ответил за отведённое время')
    except OSError as error:
        raise ConversionError(f'не удалось запустить LibreOffice: {error}')

    produced = os.path.join(work_dir, 'source.pdf')
    if not os.path.exists(produced):
        # Код возврата soffice не показатель: он отдаёт 0 и тогда, когда
        # ничего не сконвертировал (например, файл повреждён).
        detail = (result.stderr or result.stdout or b'').decode('utf-8', 'replace').strip()
        raise ConversionError(detail[:200] or 'LibreOffice не создал PDF')

    return produced
