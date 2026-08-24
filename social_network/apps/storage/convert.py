"""Конвертация офисных документов в PDF.

Нужна затем, что браузер показывает в <iframe> только PDF и картинки: .docx
справочника пользователь до этого мог лишь скачать и открыть в редакторе.

Движка два, берётся первый доступный: LibreOffice (прод на Астре, там пакет
стоит из коробки) и MS Office через COM (машина разработчика под Windows).
Питоновской библиотеки, которая верстала бы .docx сама, нет, а новых
зависимостей оба пути не приносят — PowerShell и soffice уже есть в
системе. Обоснование — ARCHITECTURE.md, раздел 13.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from django.conf import settings

# Форматы, которые берут на вход оба движка и которые встречаются в портале.
# Список белый: движки умеют больше, но обещать пользователю кнопку
# «показать в браузере» стоит только для предсказуемого результата.
CONVERTIBLE_EXTENSIONS = frozenset({
    'DOC', 'DOCX', 'ODT', 'RTF',
    'XLS', 'XLSX', 'ODS',
    'PPT', 'PPTX', 'ODP',
})

# Какое приложение Office открывает формат и каким числом обозначается в
# нём PDF (у каждого приложения своя нумерация форматов сохранения).
OFFICE_APPS = {
    'Word': (('DOC', 'DOCX', 'ODT', 'RTF'), 17),
    'Excel': (('XLS', 'XLSX', 'ODS'), 0),
    'PowerPoint': (('PPT', 'PPTX', 'ODP'), 32),
}

# Больше двух минут документ конвертируется только если движок завис:
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


def _extension(original_name: str) -> str:
    _, dot, extension = (original_name or '').rpartition('.')
    return extension.upper() if dot else ''


def is_convertible(original_name: str) -> bool:
    return _extension(original_name) in CONVERTIBLE_EXTENSIONS


def soffice_path():
    """Путь к LibreOffice или None, если его на машине нет."""
    configured = getattr(settings, 'STORAGE_SOFFICE_PATH', None)
    if configured:
        return configured if os.path.exists(configured) else None

    found = shutil.which('soffice') or shutil.which('libreoffice')
    if found:
        return found

    return next((path for path in _KNOWN_PATHS if os.path.exists(path)), None)


def office_app_for(original_name: str):
    """Какое приложение Office открывает этот формат."""
    extension = _extension(original_name)
    for app, (extensions, _) in OFFICE_APPS.items():
        if extension in extensions:
            return app
    return None


def office_app_installed(app: str) -> bool:
    """Зарегистрирован ли COM-объект приложения (Word.Application и т.п.).

    Реестр, а не поиск .exe: у Office несколько раскладок на диске
    (Program Files, ClickToRun, x86), а ProgID есть при любой из них.
    """
    if sys.platform != 'win32' or not app:
        return False

    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'{app}.Application'))
        return True
    except OSError:
        return False


def available_converter(original_name: str = None):
    """'libreoffice' | 'msoffice' | None — чем можно сконвертировать.

    LibreOffice первый: он же стоит на проде, и результат должен совпадать
    с тем, что увидят пользователи после разворачивания.
    """
    if soffice_path() is not None:
        return 'libreoffice'

    if original_name is None:
        # Спрашивают вообще про возможность конвертации, без файла.
        if any(office_app_installed(app) for app in OFFICE_APPS):
            return 'msoffice'
        return None

    if office_app_installed(office_app_for(original_name)):
        return 'msoffice'
    return None


def converter_available(original_name: str = None) -> bool:
    return available_converter(original_name) is not None


def convert_to_pdf(source_path: str, original_name: str, target_dir: str) -> str:
    """Делает из документа PDF и возвращает путь к нему в target_dir.

    Исходник копируется во временный файл С РАСШИРЕНИЕМ: на диске blob
    называется контрольной суммой без расширения, а формат оба движка
    определяют по расширению — без него они отказываются работать с файлом,
    тип которого «не распознан».
    """
    converter = available_converter(original_name)
    if converter is None:
        raise ConversionError(
            'на сервере нет ни LibreOffice, ни подходящего приложения Office'
        )

    extension = _extension(original_name).lower() or 'bin'
    work_dir = tempfile.mkdtemp(dir=target_dir)
    source_copy = os.path.join(work_dir, f'source.{extension}')
    produced = os.path.join(work_dir, 'source.pdf')
    shutil.copyfile(source_path, source_copy)

    if converter == 'libreoffice':
        _run_libreoffice(source_copy, work_dir)
    else:
        _run_msoffice(source_copy, produced, original_name)

    if not os.path.exists(produced):
        raise ConversionError('конвертер не создал PDF')

    return produced


def _run_libreoffice(source_copy: str, work_dir: str) -> None:
    # Свой профиль на каждый запуск: с общим профилем второй soffice не
    # запускается, а молча передаёт работу первому и завершается с кодом 0 —
    # задача отчиталась бы об успехе, не создав PDF.
    profile_dir = os.path.join(work_dir, 'profile')
    command = [
        soffice_path(),
        f'-env:UserInstallation=file:///{profile_dir.replace(os.sep, "/").lstrip("/")}',
        '--headless', '--norestore', '--nolockcheck', '--nodefault',
        '--convert-to', 'pdf',
        '--outdir', work_dir,
        source_copy,
    ]
    result = _run(command)

    if not os.path.exists(os.path.join(work_dir, 'source.pdf')):
        # Код возврата soffice не показатель: он отдаёт 0 и тогда, когда
        # ничего не сконвертировал (например, файл повреждён).
        raise ConversionError(_reason(result) or 'LibreOffice не создал PDF')


def _run_msoffice(source_copy: str, produced: str, original_name: str) -> None:
    """Печать в PDF средствами самого Office через COM.

    Через PowerShell, а не через pywin32: COM-мост нужен только на машине
    разработчика под Windows, и ради него не стоит заводить зависимость,
    которой на проде всё равно не будет.
    """
    app = office_app_for(original_name)
    script = _msoffice_script(app, source_copy, produced)

    script_path = os.path.join(os.path.dirname(produced), 'convert.ps1')
    # UTF-8 с BOM: PowerShell 5.1 читает файл без него в кодировке системы,
    # и путь с кириллицей в имени превращается в мусор.
    with open(script_path, 'w', encoding='utf-8-sig') as handle:
        handle.write(script)

    result = _run([
        'powershell.exe', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', script_path,
    ])

    if not os.path.exists(produced):
        raise ConversionError(_reason(result) or f'{app} не создал PDF')


def _msoffice_script(app: str, source: str, produced: str) -> str:
    source = source.replace("'", "''")
    produced = produced.replace("'", "''")
    pdf_format = OFFICE_APPS[app][1]

    # Каждое приложение открывает и сохраняет по-своему; общий у них только
    # порядок «открыть только для чтения → сохранить в PDF → закрыть».
    bodies = {
        'Word': f'''
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$app.DisplayAlerts = 0
try {{
    $doc = $app.Documents.Open('{source}', $false, $true)
    $doc.ExportAsFixedFormat('{produced}', {pdf_format})
    $doc.Close(0)
}} finally {{
    $app.Quit(0)
}}
''',
        'Excel': f'''
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
try {{
    $book = $app.Workbooks.Open('{source}', 0, $true)
    $book.ExportAsFixedFormat({pdf_format}, '{produced}')
    $book.Close($false)
}} finally {{
    $app.Quit()
}}
''',
        'PowerPoint': f'''
$app = New-Object -ComObject PowerPoint.Application
try {{
    $show = $app.Presentations.Open('{source}', $true, $false, $false)
    $show.SaveAs('{produced}', {pdf_format})
    $show.Close()
}} finally {{
    $app.Quit()
}}
''',
    }

    return "$ErrorActionPreference = 'Stop'\n" + bodies[app]


def _run(command):
    try:
        return subprocess.run(
            command, capture_output=True, timeout=CONVERT_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ConversionError('конвертер не ответил за отведённое время')
    except OSError as error:
        raise ConversionError(f'не удалось запустить конвертер: {error}')


def _reason(result) -> str:
    output = (result.stderr or result.stdout or b'')
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    return output.strip()[:200]
