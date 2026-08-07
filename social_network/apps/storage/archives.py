"""Распаковка загруженного zip в папки модуля-потребителя.

Зачем. Действующие документы организации лежат в расшаренных сетевых
папках, и перенос их на портал по одному файлу через форму загрузки никто
делать не станет — это не удобство, а условие внедрения. Отсюда требование:
загрузить архив целиком и получить на портале ту же структуру папок.

Что здесь есть и чего нет. Здесь — только работа с самим архивом: разбор,
проверки безопасности и цикл «прочитал запись → отдал в StorageService.upload()».
Создание записей потребителя (CatalogDocument, ExchangeFile, ...) и его
папок сюда не попадает: storage не знает моделей потребителей и не должен
(ARCHITECTURE.md, раздел 1). Потребитель передаёт две функции — ensure_folder
и create_record — и остаётся единственным, кто знает про свои таблицы, свои
обязательные поля и свои права. Задачи-обёртки живут у потребителей
(`catalog.tasks.import_catalog_archive` и соседи), как и задачи очистки
корзины из раздела 6.1.

Проверки. Архив приходит от пользователя, поэтому дешёвых допущений тут
нет: имена в кодировке MS-DOS, пути с '..', соотношение сжатия и общий
объём проверяются до того, как на диск ляжет первый байт.
"""
import os
import tempfile
import zipfile
from collections import namedtuple

from django.conf import settings
from django.core.files import File
from django.db import transaction

from .exceptions import FileTooLargeError, InvalidArchiveError, QuotaExceededError
from .services import StorageService

# Запись архива, прошедшая проверки: parts — каталоги внутри архива,
# name — имя файла, info — исходная запись zipfile для чтения содержимого.
ArchiveEntry = namedtuple('ArchiveEntry', 'parts name size info')

# Служебный мусор архиваторов и файловых менеджеров. Переносить его на
# портал бессмысленно, а в списке «пропущено» он был бы шумом, за которым
# не видно настоящих причин пропуска.
JUNK_NAMES = {'.ds_store', 'thumbs.db', 'desktop.ini'}
JUNK_DIRS = {'__macosx'}

# Сколько причин пропуска показывать пользователю. Результат задачи уходит
# в Celery-backend и оттуда в JSON — складывать туда тысячу строк незачем,
# первых достаточно, чтобы понять характер проблемы.
MAX_REPORTED_REASONS = 20

# Сколько раз за архив докладывать о прогрессе. Прогресс-бар на фронте
# обновляется раз в 700 мс, так что чаще полусотни шагов пользователь всё
# равно не увидит, а каждый шаг — это запись в backend Celery.
PROGRESS_STEPS = 50

NAME_MAX_LENGTH = 255  # CharField у original_name и у названий папок


def _archive_max_files() -> int:
    return getattr(settings, 'STORAGE_ARCHIVE_MAX_FILES', 1000)


def _archive_max_total_size() -> int:
    return getattr(settings, 'STORAGE_ARCHIVE_MAX_TOTAL_SIZE', 2 * 1024 * 1024 * 1024)


def _archive_max_ratio() -> int:
    return getattr(settings, 'STORAGE_ARCHIVE_MAX_RATIO', 100)


def decode_entry_name(info: zipfile.ZipInfo) -> str:
    """Имя записи в нормальной кодировке.

    Главная практическая грабля всей фичи. В zip есть флаг 0x800 «имена в
    UTF-8»; если он выставлен, `zipfile` уже декодировал имя правильно.
    Если не выставлен, стандарт предписывает cp437, и `zipfile` честно
    декодирует именно так — а windows-архиваторы (WinRAR, 7-Zip без галки
    Unicode, встроенный «Отправить → Сжатая папка» старых версий) писали
    русские имена в cp866. В результате «Приказы 2024.pdf» приезжает как
    «╧ЁштъЁ√ 2024.pdf».

    Ровно такие архивы и придут: их делали на рабочих станциях из тех самых
    сетевых папок, ради переноса которых всё это и написано. Поэтому имя
    без флага UTF-8 перекодируется обратно в байты по cp437 и читается как
    cp866. Для чистого ASCII преобразование ничего не меняет: в диапазоне
    0-127 обе кодировки совпадают.
    """
    if info.flag_bits & 0x800:
        return info.filename

    try:
        return info.filename.encode('cp437').decode('cp866')
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Имя не укладывается в предположение — оставляем как есть.
        # Кривое имя лучше, чем отказ импортировать файл.
        return info.filename


def _truncate(name: str) -> str:
    """Обрезает имя до длины поля модели, сохраняя расширение — иначе
    длинное имя из архива роняло бы вставку с DataError, а обрезка «в лоб»
    превращала бы «договор.pdf» в файл без расширения."""
    if len(name) <= NAME_MAX_LENGTH:
        return name

    stem, suffix = os.path.splitext(name)
    return stem[:NAME_MAX_LENGTH - len(suffix)] + suffix


def split_entry_path(raw_name: str):
    """(каталоги, имя файла) или None, если запись импортировать не следует.

    Пути с '..' отбрасываются целиком. Файловая система от них тут, строго
    говоря, не страдает — содержимое всё равно ляжет в blobs/ под именем
    checksum, — но имена из архива становятся НАЗВАНИЯМИ ПАПОК модуля, и
    пускать в них обход дерева незачем.
    """
    parts = []
    for part in raw_name.replace('\\', '/').split('/'):
        part = part.strip()
        if not part or part == '.':
            # Пустой сегмент — это ведущий '/' (абсолютный путь) или
            # двойной слэш; и то и другое просто выпрямляем.
            continue
        if part == '..' or ':' in part:
            return None
        parts.append(_truncate(part))

    if not parts:
        return None

    if parts[0].lower() in JUNK_DIRS or parts[-1].lower() in JUNK_NAMES:
        return None

    return tuple(parts[:-1]), parts[-1]


def inspect_archive(source) -> list:
    """Проверяет архив целиком и возвращает список записей к импорту.

    Вызывается вьюхой ДО постановки задачи: читается только оглавление
    архива, не содержимое, поэтому проверка дешёвая даже на большом файле.
    Пользователь узнаёт «это не архив» или «архив зашифрован» сразу в ответ
    на загрузку, а не через опрос статуса задачи.

    source — путь к файлу или файловый объект.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile:
        raise InvalidArchiveError('Файл не является zip-архивом или повреждён')

    entries = []
    total_size = 0
    total_compressed = 0

    for info in infos:
        if info.flag_bits & 0x1:
            # Пароль спросить негде и хранить его негде. Молча пропустить
            # такие записи нельзя: пользователь получил бы пустую папку и
            # никакого объяснения.
            raise InvalidArchiveError(
                'Архив защищён паролем — распаковать его на сайте нельзя. '
                'Пересоберите архив без пароля.'
            )

        if info.is_dir():
            continue

        split = split_entry_path(decode_entry_name(info))
        if split is None:
            continue

        parts, name = split
        entries.append(ArchiveEntry(parts=parts, name=name, size=info.file_size, info=info))
        total_size += info.file_size
        total_compressed += info.compress_size

    if not entries:
        raise InvalidArchiveError('В архиве нет файлов для распаковки')

    max_files = _archive_max_files()
    if len(entries) > max_files:
        raise InvalidArchiveError(
            f'В архиве {len(entries)} файлов при пределе {max_files}. '
            f'Разбейте архив на части.'
        )

    max_total = _archive_max_total_size()
    if total_size > max_total:
        raise InvalidArchiveError(
            f'В распакованном виде архив занимает {_mb(total_size)} МБ '
            f'при пределе {_mb(max_total)} МБ. Разбейте архив на части.'
        )

    # Соотношение сжатия ловит zip-бомбу: несколько килобайт архива,
    # разворачивающиеся в гигабайты. Предел по объёму выше её тоже поймал
    # бы, но только после того, как мы прочитаем распакованный поток, —
    # а здесь отказ происходит по оглавлению, до чтения содержимого.
    ratio = total_size / max(total_compressed, 1)
    if ratio > _archive_max_ratio():
        raise InvalidArchiveError(
            'Архив выглядит как «zip-бомба»: заявленный объём после '
            'распаковки несоразмерен размеру файла.'
        )

    return entries


def _mb(size: int) -> int:
    return round(size / (1024 * 1024))


def extract_archive(
    source, *, user, category, root_folder, ensure_folder, create_record, on_progress=None,
) -> dict:
    """Распаковывает архив, создавая папки и записи через колбэки потребителя.

    ensure_folder(parent, name) -> папка модуля; вызывается только для
    каталогов ИЗ архива, корневая папка приходит готовой (root_folder).
    Результат запоминается по пути, поэтому на сто файлов в одной папке
    придётся один вызов, а не сто.

    create_record(file_object, folder, name) -> None; создаёт запись
    потребителя. Права здесь не проверяются: вьюха уже убедилась, что
    пользователь вправе писать в root_folder, а всё остальное — вложенность
    внутри неё.

    Возвращает сводку в тех же ключах done/total, что и остальные фоновые
    задачи (storage.tasks), чтобы прогресс на фронте работал без правок.
    """
    entries = inspect_archive(source)

    folders = {(): root_folder}
    created_files = 0
    skipped = 0
    reasons = []
    step = max(1, len(entries) // PROGRESS_STEPS)

    def report(name, reason):
        nonlocal skipped
        skipped += 1
        if len(reasons) < MAX_REPORTED_REASONS:
            reasons.append(f'«{name}» — {reason}')

    with zipfile.ZipFile(source) as archive:
        for index, entry in enumerate(entries, start=1):
            try:
                folder = _ensure_path(folders, entry.parts, ensure_folder)
            except Exception as error:  # noqa: BLE001 — причина уходит в отчёт
                report('/'.join(entry.parts), f'не удалось создать папку: {error}')
                continue

            try:
                with _entry_as_upload(archive, entry) as uploaded:
                    # upload() и create_record в одной транзакции: если
                    # запись потребителя не создастся, FileObject не должен
                    # остаться висеть ACTIVE без единой ссылки — это ровно
                    # тот случай «файл живёт вечно», ради которого написан
                    # раздел 5.5. Файл на диске при откате останется, его
                    # найдёт storage_verify (5.4).
                    with transaction.atomic():
                        file_object = StorageService.upload(
                            uploaded, user=user, category=category,
                        )
                        create_record(file_object, folder, entry.name)
            except FileTooLargeError:
                report(entry.name, 'файл слишком большой')
                continue
            except QuotaExceededError:
                report(entry.name, 'превышена квота хранилища')
                continue
            except zipfile.BadZipFile as error:
                # Битая запись — не повод потерять остальной архив. Сюда же
                # попадает расхождение с оглавлением: zipfile сверяет CRC и
                # объём распакованного с заявленными в заголовке, так что
                # запись, соврамшая о своём размере, до upload() не доходит
                # вовсе. Без этой ветки один повреждённый файл ронял задачу
                # целиком, и пользователь получал частичный импорт с
                # непонятной ошибкой.
                report(entry.name, f'запись повреждена ({error})')
                continue

            created_files += 1

            # Прогресс докладывается не чаще PROGRESS_STEPS раз за архив:
            # каждый вызов update_state() — это запись в backend Celery, и
            # на тысяче файлов тысяча лишних обращений к Redis стоила бы
            # заметно дороже самой распаковки мелких документов.
            if on_progress is not None and (index % step == 0 or index == len(entries)):
                on_progress(index, len(entries))

    return {
        'done': created_files,
        'total': len(entries),
        'folders': len(folders) - 1,  # root_folder не создавали
        'skipped': skipped,
        'reasons': reasons,
    }


def _ensure_path(folders: dict, parts: tuple, ensure_folder):
    """Создаёт (или находит) всю цепочку папок пути и возвращает последнюю."""
    for depth in range(1, len(parts) + 1):
        key = parts[:depth]
        if key not in folders:
            folders[key] = ensure_folder(folders[key[:-1]], key[-1])
    return folders[parts]


class _entry_as_upload:
    """Запись архива в виде объекта, который принимает StorageService.upload().

    Через SpooledTemporaryFile, а не через archive.read() в память:
    отдельный файл внутри архива ограничен STORAGE_MAX_UPLOAD_SIZE (100 МБ),
    и держать столько в памяти воркера на каждой итерации незачем. Мелкие
    файлы — а их подавляющее большинство — так и остаются в памяти, крупные
    сами уезжают на диск.

    Предел проверяется по ФАКТИЧЕСКИ прочитанным байтам, а не по
    info.file_size: размер в оглавлении архива заявляет тот, кто архив
    собрал, и запись «1 байт», разворачивающаяся в гигабайт, прошла бы и
    предварительную проверку объёма, и валидацию в upload(). Здесь чтение
    обрывается на превышении, то есть лишнее просто не попадает ни в
    память, ни на диск.
    """

    SPOOL_LIMIT = 8 * 1024 * 1024
    READ_CHUNK = 1024 * 1024

    def __init__(self, archive, entry):
        self.archive = archive
        self.entry = entry
        self.buffer = None

    def __enter__(self):
        limit = getattr(settings, 'STORAGE_MAX_UPLOAD_SIZE', 100 * 1024 * 1024)

        self.buffer = tempfile.SpooledTemporaryFile(max_size=self.SPOOL_LIMIT)
        written = 0

        # Исключение из __enter__ не вызывает __exit__, поэтому буфер
        # закрывается здесь же — иначе на каждом слишком большом файле
        # оставался бы незакрытый временный файл.
        try:
            with self.archive.open(self.entry.info) as source:
                while True:
                    chunk = source.read(self.READ_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise FileTooLargeError(
                            f'«{self.entry.name}» превышает максимальный размер {limit} байт'
                        )
                    self.buffer.write(chunk)
        except Exception:
            self.buffer.close()
            raise

        self.buffer.seek(0)
        uploaded = File(self.buffer, name=self.entry.name)
        # size — cached_property у File, на SpooledTemporaryFile она
        # вычислялась бы лишним проходом seek/tell. Присваиваем измеренное.
        uploaded.size = written
        return uploaded

    def __exit__(self, *exc_info):
        self.buffer.close()
        return False
