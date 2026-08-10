import hashlib
import mimetypes
import os
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .exceptions import ArchiveTooLargeError, FileTooLargeError, QuotaExceededError
from .models import FileBlob, FileObject, StorageAuditLog
from .utils import is_inline_safe

CHUNK_SIZE = 1024 * 1024  # 1 МБ


# Настройки читаются при каждом вызове, а не один раз при импорте модуля.
#
# Раньше это были константы уровня модуля (MAX_UPLOAD_SIZE = getattr(...)),
# вычислявшиеся при загрузке приложения. Последствия: override_settings в
# тестах на них не действовал вообще — тест квоты был вынужден подменять
# атрибут модуля руками (services_module.USER_QUOTA = 100) и возвращать
# его обратно в finally; а изменение квоты в settings требовало
# перезапуска процесса, хотя ARCHITECTURE.md (раздел 9) обещает смену
# «одной строкой без миграций».
def _max_upload_size() -> int:
    return getattr(settings, 'STORAGE_MAX_UPLOAD_SIZE', 100 * 1024 * 1024)


def _user_quota():
    return getattr(settings, 'STORAGE_USER_QUOTA', None)


def _orphan_retention_days() -> int:
    return getattr(settings, 'STORAGE_ORPHAN_RETENTION_DAYS', 7)


def _category_ttl() -> dict:
    return getattr(settings, 'STORAGE_CATEGORY_TTL', {})


def _zip_max_files() -> int:
    return getattr(settings, 'STORAGE_ZIP_MAX_FILES', 200)


def _zip_max_total_size() -> int:
    return getattr(settings, 'STORAGE_ZIP_MAX_TOTAL_SIZE', 1024 * 1024 * 1024)


def _format_size(size: int) -> str:
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if size < 1024 or unit == 'ГБ':
            return f'{size:.0f} {unit}' if unit == 'Б' else f'{size:.1f} {unit}'
        size /= 1024


def _content_disposition(disposition_type: str, name: str) -> str:
    """Значение заголовка Content-Disposition с корректно оформленным именем.

    ASCII — обычный filename="...", иначе RFC 5987 (filename*=utf-8''...).
    Подставить кириллицу напрямую нельзя: HttpResponse кодирует не-latin1
    значение заголовка по RFC 2047 (=?utf-8?b?...?=), а Content-Disposition
    этой формы не понимает — браузер сохранил бы файл под base64-строкой.
    В ветке DEBUG то же самое делает за нас FileResponse, поэтому локально
    баг не проявлялся: он есть только на прод-пути.
    """
    try:
        name.encode('ascii')
        file_expr = 'filename="{}"'.format(name.replace('\\', '\\\\').replace('"', r'\"'))
    except UnicodeEncodeError:
        file_expr = "filename*=utf-8''{}".format(quote(name))
    return f'{disposition_type}; {file_expr}'


class _ZipStreamBuffer:
    """Файл-объект, который ничего не хранит: zipfile пишет сюда, а
    генератор тут же забирает накопленное и отдаёт наружу.

    tell() нужен: без него zipfile оборачивает поток в собственный _Tellable.
    seek() намеренно НЕТ — по его отсутствию zipfile понимает, что поток
    неперемотываемый, и записывает размеры и CRC в data descriptor ПОСЛЕ
    содержимого, вместо того чтобы возвращаться и править уже отданный
    заголовок. Именно это и делает потоковую сборку возможной.
    """

    def __init__(self):
        self._chunks = []
        self._position = 0

    def write(self, data) -> int:
        self._chunks.append(bytes(data))
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = b''.join(self._chunks)
        self._chunks = []
        return data


def _archive_name(path: tuple, original_name: str, used: set) -> str:
    """Полное имя записи внутри архива: <путь>/<файл>, уникальное.

    Имя файла отрезается от пути, потому что original_name пришёл из
    браузера и в нём может оказаться что угодно, вплоть до '../../'.
    Каталоги берутся не оттуда, а из дерева папок модуля (path), поэтому
    им доверять можно — но и они прогоняются через ту же чистку, чтобы
    разделитель из названия папки не превратился во вложенность.

    Уникальность проверяется по ПОЛНОМУ пути, а не по имени: два файла с
    одинаковым названием в разных папках — норма и разводить их не нужно,
    а два одинаковых имени в одной папке оставили бы пользователю один
    файл вместо двух (дедупликация делает такие пары обычным делом).
    """
    def clean(value):
        return os.path.basename(str(value or '').replace('\\', '/')).strip()

    directory = '/'.join(filter(None, (clean(part) for part in path)))
    name = clean(original_name) or 'файл'

    stem, suffix = os.path.splitext(name)
    index = 2
    candidate = f'{directory}/{name}' if directory else name

    while candidate.lower() in used:
        numbered = f'{stem} ({index}){suffix}'
        candidate = f'{directory}/{numbered}' if directory else numbered
        index += 1

    used.add(candidate.lower())
    return candidate


def _iter_zip(items):
    """Генератор байтов zip-архива. Память не растёт с размером архива:
    в буфере одновременно живёт не больше одного прочитанного чанка."""
    import zipfile

    buffer = _ZipStreamBuffer()
    used_names = set()

    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive:
        for path, file_object in items:
            entry = zipfile.ZipInfo(
                _archive_name(path, file_object.original_name, used_names),
                date_time=timezone.localtime(file_object.uploaded_at).timetuple()[:6],
            )
            entry.compress_type = zipfile.ZIP_STORED

            with archive.open(entry, 'w') as target, file_object.blob.file.open('rb') as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                    target.write(chunk)
                    data = buffer.take()
                    if data:
                        yield data

            data = buffer.take()
            if data:
                yield data

    # Центральный каталог архива — пишется на выходе из with.
    yield buffer.take()


def _sha256(uploaded_file) -> str:
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks(CHUNK_SIZE):
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def _acquire_checksum_lock(checksum: str) -> None:
    """Postgres advisory lock на числовое представление checksum — держится
    до конца текущей транзакции (pg_advisory_xact_lock), снимается сам при
    COMMIT/ROLLBACK. В отличие от select_for_update(), блокирует ДО того, как
    строка с этим checksum появится в таблице, что и нужно для сериализации
    двух конкурентных upload() одного и того же нового файла. На бэкендах без
    поддержки advisory lock (например, SQLite при локальном запуске без
    Postgres) вызов тихо ничего не делает — единственная защита от гонки в
    этом случае остаётся на уровне ORM-транзакции, чего для SQLite всё равно
    недостаточно (см. ARCHITECTURE.md и storage/tests/test_concurrency.py)."""
    from django.db import connection as db_connection

    if db_connection.vendor != 'postgresql':
        return

    lock_key = int(checksum[:15], 16)  # умещается в bigint
    with db_connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])


def _audit(action, *, checksum, user=None, consumer='', original_name=''):
    StorageAuditLog.objects.create(
        action=action, checksum=checksum, user=user, consumer=consumer, original_name=original_name,
    )


def _iter_consumer_fields():
    """Возвращает пары (модель, имя поля) для всех FK на FileObject во всём
    проекте.

    Намеренно обходит apps.get_models(), а НЕ FileObject._meta.get_fields():
    все потребители объявляют FK с related_name='+', который подавляет
    создание обратной связи, поэтому get_fields() не возвращает по ним
    ничего и проверка ссылок всегда давала False (см. регрессионный тест
    ReferenceDetectionTest). Обход моделей от related_name не зависит."""
    from django.apps import apps

    for model in apps.get_models():
        if model._meta.app_label == 'storage':
            continue
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model is FileObject:
                yield model, field.name


def _object_has_references(file_object) -> bool:
    """Проверяет, ссылается ли на file_object хоть одна модель-потребитель.
    Регистрировать модели где-либо не требуется — они находятся
    интроспекцией (ARCHITECTURE.md, раздел 5.2)."""
    for model, field_name in _iter_consumer_fields():
        if model._default_manager.filter(**{field_name: file_object}).exists():
            return True
    return False


class StorageService:

    @staticmethod
    def get_usage(user) -> int:
        """Сумма размеров УНИКАЛЬНЫХ blob'ов, на которые ссылаются FileObject
        пользователя — дедуплицированный файл не считается дважды."""
        blob_ids = FileObject.objects.filter(uploaded_by=user).values_list('blob_id', flat=True).distinct()
        total = FileBlob.objects.filter(pk__in=blob_ids).aggregate(total=Sum('size'))['total']
        return total or 0

    @staticmethod
    def get_storage_stats() -> dict:
        """Сводка по всему хранилищу для dashboard'а (раздел «Занятое
        место»): сколько реально занято на диске (ACTIVE + ORPHAN — сироты
        физически ещё не удалены и тоже занимают место) и сколько будет
        освобождено ближайшей ежедневной очисткой."""
        active = FileBlob.objects.filter(status=FileBlob.Status.ACTIVE).aggregate(
            total=Sum('size'), count=Count('pk'),
        )
        orphan = FileBlob.objects.filter(status=FileBlob.Status.ORPHAN).aggregate(
            total=Sum('size'), count=Count('pk'),
        )
        return {
            'active_size': active['total'] or 0,
            'active_count': active['count'] or 0,
            'orphan_size': orphan['total'] or 0,
            'orphan_count': orphan['count'] or 0,
            'total_size': (active['total'] or 0) + (orphan['total'] or 0),
        }

    @staticmethod
    @transaction.atomic
    def upload(uploaded_file, *, user, category) -> FileObject:
        max_upload_size = _max_upload_size()
        if uploaded_file.size > max_upload_size:
            raise FileTooLargeError(
                f'Файл превышает максимальный размер {max_upload_size} байт'
            )

        quota = _user_quota()
        if quota is not None:
            current_usage = StorageService.get_usage(user)
            if current_usage + uploaded_file.size > quota:
                raise QuotaExceededError(
                    f'Загрузка превысит квоту пользователя ({quota} байт)'
                )

        checksum = _sha256(uploaded_file)
        mime_type = mimetypes.guess_type(uploaded_file.name)[0] or ''

        # select_for_update() блокирует существующую строку, но если blob'а
        # с этим checksum ещё нет — блокировать нечего, и два параллельных
        # upload() одинакового содержимого оба пройдут "blob is None" и оба
        # физически запишут файл на диск, прежде чем один из них словит
        # IntegrityError в БД (на диске останутся дубликаты с суффиксами от
        # FileSystemStorage, даже если в БД лишняя запись не создалась).
        # Advisory lock на числовое представление checksum сериализует
        # именно эту гонку: конкурирующие upload() одного и того же
        # содержимого ждут друг друга ДО записи на диск, а upload() разных
        # файлов друг другу не мешают.
        _acquire_checksum_lock(checksum)

        blob = FileBlob.objects.select_for_update().filter(checksum=checksum).first()

        if blob:
            if blob.status == FileBlob.Status.ORPHAN:
                blob.status = FileBlob.Status.ACTIVE
                blob.orphaned_at = None
                blob.save(update_fields=['status', 'orphaned_at'])
                _audit(StorageAuditLog.Action.RESTORE, checksum=checksum, user=user)
        else:
            try:
                # Вложенный atomic() создаёт savepoint: если create() упадёт
                # с IntegrityError, откатывается только этот savepoint —
                # внешняя транзакция остаётся годной для дальнейших запросов.
                # Без этого Postgres помечает всю транзакцию "испорченной"
                # после первой же ошибки и следующий SELECT падает с
                # TransactionManagementError вместо штатной обработки.
                # IntegrityError теперь возможен только если advisory lock
                # почему-то не сработал (например, на бэкенде без его
                # поддержки) — эта ветка остаётся как страховка.
                with transaction.atomic():
                    blob = FileBlob.objects.create(
                        file=File(uploaded_file, name=checksum),
                        checksum=checksum,
                        size=uploaded_file.size,
                        mime_type=mime_type,
                    )
                _audit(StorageAuditLog.Action.UPLOAD, checksum=checksum, user=user)
            except IntegrityError:
                # Конкурентный аплоад того же содержимого опередил нас —
                # берём его запись, свою копию не создаём (ARCHITECTURE.md, 5.3).
                blob = FileBlob.objects.select_for_update().get(checksum=checksum)

        return FileObject.objects.create(
            blob=blob, original_name=uploaded_file.name, category=category, uploaded_by=user,
        )

    @staticmethod
    def copy_reference(file_object: FileObject, *, user, category, original_name=None) -> FileObject:
        """Создаёт новый FileObject на ТОТ ЖЕ blob — для копирования/пересылки
        файла между модулями (например, документ каталога → обменник).

        Не путать с upload(): здесь нет ни содержимого для загрузки, ни
        записи на диск — дедупликация была бы избыточной, файл уже лежит на
        диске под своим checksum. Копия — это просто вторая именованная
        ссылка на тот же blob, ровно то отношение blob/object, ради которого
        эти две модели разведены (ARCHITECTURE.md, раздел 2).

        Квота пользователя всё равно проверяется: get_usage() считает по
        уникальным blob'ам, так что если этот же blob уже "стоит" в квоте
        пользователя (например, он сам его когда-то загрузил), повторное
        копирование ничего не добавит — но если это чужой файл, который
        пользователь не грузил, копия ляжет в его квоту как новое использование.
        """
        quota = _user_quota()
        if quota is not None:
            current_usage = StorageService.get_usage(user)
            already_counted = FileObject.objects.filter(
                uploaded_by=user, blob_id=file_object.blob_id
            ).exists()
            if not already_counted and current_usage + file_object.blob.size > quota:
                raise QuotaExceededError(
                    f'Копирование превысит квоту пользователя ({quota} байт)'
                )

        with transaction.atomic():
            # Блокировка blob'а и перечитывание статуса — по той же причине,
            # что и в upload(). Без неё параллельный detach() мог успеть
            # перевести blob в ORPHAN между чтением file_object и созданием
            # копии: ссылка получилась бы живой, а blob навсегда остался бы
            # в статусе «ожидает удаления» и попадал в отчёт занятого места
            # как подлежащий очистке. Файл при этом не терялся
            # (purge_expired_orphans проверяет наличие ссылок), но состояние
            # было заведомо неверным.
            blob = FileBlob.objects.select_for_update().get(pk=file_object.blob_id)
            if blob.status == FileBlob.Status.ORPHAN:
                blob.status = FileBlob.Status.ACTIVE
                blob.orphaned_at = None
                blob.save(update_fields=['status', 'orphaned_at'])
                _audit(StorageAuditLog.Action.RESTORE, checksum=blob.checksum, user=user)

            return FileObject.objects.create(
                blob=blob,
                original_name=original_name or file_object.original_name,
                category=category,
                uploaded_by=user,
            )

    @staticmethod
    @transaction.atomic
    def detach(file_object: FileObject, *, user=None, consumer='') -> None:
        """Снимает ссылку на file_object. Если ссылок на blob больше не
        осталось — переводит blob в ORPHAN. Ничего не удаляет физически
        (см. purge_expired_orphans).

        При УДАЛЕНИИ строки потребителя вызывать вручную не нужно: это делает
        сигнал post_delete (storage/signals.py), иначе каскадные удаления
        оставляли бы blob'ы в ACTIVE навсегда. Публичный API нужен для
        случая, когда ссылка снимается без удаления строки — например,
        phonebook подменяет file_object на живой записи при замене файла.
        """
        if file_object.pk is None or not FileObject.objects.filter(pk=file_object.pk).exists():
            # Уже отвязан (например, сигналом при каскаде) — повторный вызов
            # не должен писать в журнал второй раз и сбрасывать orphaned_at,
            # иначе blob каждый раз начинал бы отсчёт срока заново.
            return

        blob = FileBlob.objects.select_for_update().get(pk=file_object.blob_id)
        checksum = blob.checksum

        _audit(
            StorageAuditLog.Action.DETACH, checksum=checksum, user=user,
            consumer=consumer, original_name=file_object.original_name,
        )

        if _object_has_references(file_object):
            return

        file_object.delete()

        if not blob.file_objects.exists():
            blob.status = FileBlob.Status.ORPHAN
            blob.orphaned_at = timezone.now()
            blob.save(update_fields=['status', 'orphaned_at'])

    @staticmethod
    def purge_expired_orphans() -> int:
        """Физически удаляет blob'ы, пробывшие в ORPHAN дольше
        STORAGE_ORPHAN_RETENTION_DAYS. Каждый blob обрабатывается в своей
        транзакции с повторной проверкой статуса под блокировкой — за время
        обхода списка кандидатов blob мог быть воскрешён через upload()
        (ARCHITECTURE.md, раздел 5.3)."""
        deadline = timezone.now() - timedelta(days=_orphan_retention_days())
        candidate_ids = list(
            FileBlob.objects.filter(
                status=FileBlob.Status.ORPHAN, orphaned_at__lte=deadline
            ).values_list('pk', flat=True)
        )

        purged = 0
        for blob_id in candidate_ids:
            with transaction.atomic():
                blob = FileBlob.objects.select_for_update().filter(pk=blob_id).first()
                if not blob or blob.status != FileBlob.Status.ORPHAN:
                    continue
                if blob.orphaned_at is None or blob.orphaned_at > deadline:
                    continue
                if blob.file_objects.exists():
                    continue

                _audit(StorageAuditLog.Action.PURGE, checksum=blob.checksum, user=None)
                blob.file.delete(save=False)
                blob.delete()
                purged += 1

        return purged

    @staticmethod
    def get_category_ttl_days(category) -> int | None:
        """Срок хранения категории в днях из STORAGE_CATEGORY_TTL, None =
        бессрочно. storage предоставляет только саму политику — применяют её
        сами модули-потребители, удаляя свои записи и вызывая detach()
        (см. exchange.tasks.cleanup_expired_exchange_files).

        Своей задачи "удалить всё истёкшее" у storage намеренно НЕТ: она
        вызывала бы detach() для FileObject, на который ещё ссылается живая
        запись потребителя, и не удаляла бы ничего (detach() такие файлы
        защищает), но при этом отчитывалась об успехе. Удалять же чужие
        записи storage не должен — он не знает бизнес-логику потребителей
        (ARCHITECTURE.md, раздел 1)."""
        return _category_ttl().get(category)

    @staticmethod
    def get_download_response(file_object: FileObject, request, *, inline: bool = False):
        """Права доступа НЕ проверяются здесь — вызывающий модуль обязан
        проверить их до вызова (ARCHITECTURE.md, раздел 8).

        inline=True — 'Content-Disposition: inline' вместо 'attachment', для
        файлов, которые должны открываться прямо в браузере/iframe (PDF
        справочника), а не скачиваться. Просьба открыть inline тип, который
        для этого небезопасен, молча понижается до вложения (см. ниже)."""
        from django.http import FileResponse, HttpResponse

        blob = file_object.blob

        # Открыть в браузере можно только заведомо пассивный тип. Иначе
        # загруженный .html (или .svg — там тоже работает <script>)
        # открывался бы как страница в домене портала: обычная загрузка
        # файла в обменник превращалась бы в хранимую XSS. Проверка стоит
        # здесь, а не во вьюхах, потому что ?inline=1 пробрасывают все три
        # модуля-потребителя плюс phonebook, и договорённость «каждый
        # проверяет сам» разошлась бы при первом же новом потребителе.
        #
        # Отказ молчаливый, а не 403: в интерфейсе пункт «Просмотр» для
        # таких типов не показывается вовсе (utils.PREVIEWABLE_EXTENSIONS),
        # то есть сюда попадает только ссылка, собранная руками. Отдать
        # файл вложением — ровно то, что и должно произойти по такой
        # ссылке, ошибки здесь нет.
        if inline and not is_inline_safe(blob.mime_type):
            inline = False

        disposition_type = 'inline' if inline else 'attachment'

        if settings.DEBUG:
            response = FileResponse(
                blob.file.open('rb'), as_attachment=not inline, filename=file_object.original_name,
            )
            # Тот же заголовок ставит nginx на /protected/ (deploy/nginx/
            # portal.conf), но полагаться только на конфиг веб-сервера
            # нельзя: в разработке nginx'а нет, а на проде он может быть
            # развёрнут не из этого файла.
            response['X-Content-Type-Options'] = 'nosniff'
            return response

        response = HttpResponse()
        response['Content-Type'] = blob.mime_type or 'application/octet-stream'
        response['X-Content-Type-Options'] = 'nosniff'

        response['Content-Disposition'] = _content_disposition(
            disposition_type, file_object.original_name,
        )

        internal_path = default_storage.path(blob.file.name)
        media_root = str(settings.MEDIA_ROOT)
        relative = os.path.relpath(internal_path, media_root).replace(os.sep, '/')
        response['X-Accel-Redirect'] = f'/protected/{relative}'
        return response

    @staticmethod
    def get_archive_limits() -> tuple:
        """(максимум файлов, максимум суммарного размера) для zip-выгрузки."""
        return _zip_max_files(), _zip_max_total_size()

    @staticmethod
    def check_archive_request(items) -> dict:
        """Проверяет, что архив можно собрать, и возвращает его сводку.

        items — последовательность пар (путь, FileObject), где путь это
        кортеж каталогов внутри архива. У массового скачивания выбранного
        путь пустой (файлы ложатся в корень архива), у скачивания папки —
        её дерево.

        Отдельный метод, а не проверка внутри get_archive_response(), потому
        что фронт обязан узнать причину отказа ДО начала скачивания:
        браузер уходит по ссылке на архив обычной навигацией, и ответ с
        ошибкой в этот момент показать уже нечем — он просто заменит собой
        страницу. Поэтому кнопка сначала спрашивает разрешение (?check=1),
        и только потом переходит по ссылке.
        """
        max_files, max_total = StorageService.get_archive_limits()

        count = len(items)
        if not count:
            raise ArchiveTooLargeError('Нечего скачивать: не выбрано ни одного файла')

        if count > max_files:
            raise ArchiveTooLargeError(
                f'Слишком много файлов: {count} при пределе {max_files}. '
                f'Скачайте частями.'
            )

        # По уникальным blob'ам: дедупликация не уменьшает размер архива
        # (каждый файл лежит в нём отдельной записью под своим именем),
        # поэтому здесь считается именно сумма по объектам, а не по blob'ам,
        # как в get_usage().
        total_size = sum(file_object.blob.size for _path, file_object in items)
        if total_size > max_total:
            raise ArchiveTooLargeError(
                f'Слишком большой архив: {_format_size(total_size)} при пределе '
                f'{_format_size(max_total)}. Скачайте частями.'
            )

        return {'count': count, 'total_size': total_size}

    @staticmethod
    def get_archive_response(items, *, filename: str):
        """Отдаёт выбранные файлы одним zip, собирая его НА ЛЕТУ.

        Права не проверяются здесь — вызывающий модуль обязан сузить список
        до разрешённого (ARCHITECTURE.md, раздел 8), ровно как и для
        get_download_response().

        Почему стриминг, а не «собрать и отдать»: собранный в память архив
        на сотню документов — это сотня мегабайт на КАЖДЫЙ параллельный
        запрос, а собранный во временный файл требует места на диске,
        уборки за собой и всё равно задерживает первый байт до конца
        сборки. Здесь zipfile пишет в буфер-обманку, а генератор сразу
        отдаёт накопленное наружу: память не растёт с размером архива.

        ZIP_STORED, без сжатия: содержимое портала — это pdf/docx/xlsx и
        картинки, то есть уже сжатые форматы, на которых deflate даёт
        единицы процентов и стоит процессорного времени того же воркера,
        который в это время держит соединение.
        """
        from django.http import StreamingHttpResponse

        response = StreamingHttpResponse(
            _iter_zip(items), content_type='application/zip',
        )
        response['Content-Disposition'] = _content_disposition('attachment', filename)
        response['X-Content-Type-Options'] = 'nosniff'
        # Content-Length неизвестен: архив ещё не собран. Браузер покажет
        # скачивание без процента — это цена того, что первый байт уходит
        # сразу, а не после сборки всего архива.
        return response

    @staticmethod
    def find_untracked_files() -> list:
        """Обходит MEDIA_ROOT/storage/blobs/ и возвращает пути файлов, для
        которых нет записи FileBlob с соответствующим checksum. Не удаляет
        ничего — только отчёт (см. management-команду storage_verify)."""
        blobs_root = os.path.join(str(settings.MEDIA_ROOT), 'storage', 'blobs')
        if not os.path.isdir(blobs_root):
            return []

        known_checksums = set(FileBlob.objects.values_list('checksum', flat=True))
        untracked = []

        for dirpath, _dirnames, filenames in os.walk(blobs_root):
            for filename in filenames:
                if filename not in known_checksums:
                    untracked.append(os.path.join(dirpath, filename))

        return untracked
