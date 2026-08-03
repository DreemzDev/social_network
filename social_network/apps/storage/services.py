import hashlib
import mimetypes
import os
from datetime import timedelta

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .exceptions import FileTooLargeError, QuotaExceededError
from .models import FileBlob, FileObject, StorageAuditLog

CHUNK_SIZE = 1024 * 1024  # 1 МБ

MAX_UPLOAD_SIZE = getattr(settings, 'STORAGE_MAX_UPLOAD_SIZE', 100 * 1024 * 1024)
USER_QUOTA = getattr(settings, 'STORAGE_USER_QUOTA', None)
ORPHAN_RETENTION_DAYS = getattr(settings, 'STORAGE_ORPHAN_RETENTION_DAYS', 7)
CATEGORY_TTL = getattr(settings, 'STORAGE_CATEGORY_TTL', {})


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
    @transaction.atomic
    def upload(uploaded_file, *, user, category) -> FileObject:
        if uploaded_file.size > MAX_UPLOAD_SIZE:
            raise FileTooLargeError(
                f'Файл превышает максимальный размер {MAX_UPLOAD_SIZE} байт'
            )

        if USER_QUOTA is not None:
            current_usage = StorageService.get_usage(user)
            if current_usage + uploaded_file.size > USER_QUOTA:
                raise QuotaExceededError(
                    f'Загрузка превысит квоту пользователя ({USER_QUOTA} байт)'
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
        deadline = timezone.now() - timedelta(days=ORPHAN_RETENTION_DAYS)
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
        return CATEGORY_TTL.get(category)

    @staticmethod
    def get_download_response(file_object: FileObject, request, *, inline: bool = False):
        """Права доступа НЕ проверяются здесь — вызывающий модуль обязан
        проверить их до вызова (ARCHITECTURE.md, раздел 8).

        inline=True — 'Content-Disposition: inline' вместо 'attachment', для
        файлов, которые должны открываться прямо в браузере/iframe (PDF
        справочника), а не скачиваться."""
        from django.http import FileResponse, HttpResponse

        blob = file_object.blob
        disposition_type = 'inline' if inline else 'attachment'

        if settings.DEBUG:
            response = FileResponse(
                blob.file.open('rb'), as_attachment=not inline, filename=file_object.original_name,
            )
            return response

        response = HttpResponse()
        response['Content-Type'] = blob.mime_type or 'application/octet-stream'
        response['Content-Disposition'] = f'{disposition_type}; filename="{file_object.original_name}"'
        internal_path = default_storage.path(blob.file.name)
        media_root = str(settings.MEDIA_ROOT)
        relative = os.path.relpath(internal_path, media_root).replace(os.sep, '/')
        response['X-Accel-Redirect'] = f'/protected/{relative}'
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
