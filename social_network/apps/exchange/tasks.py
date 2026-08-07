from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from storage import realtime
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .models import ExchangeFile


@shared_task
def cleanup_expired_exchange_files():
    """Удаляет файлы обменника старше срока хранения категории — аналог
    очистки старых сетевых папок-обменников по расписанию.

    Очистка живёт здесь, а не в storage: storage не знает, что запись
    ExchangeFile можно удалять по возрасту, и не должен трогать чужие
    таблицы. Его задача — только отдать политику (get_category_ttl_days) и
    корректно отработать detach() (ARCHITECTURE.md, раздел 1).

    Файлы в корзине тоже удаляются: попадание в корзину не продлевает срок
    хранения, иначе обменник можно засорить, просто ничего не восстанавливая.
    """
    ttl_days = StorageService.get_category_ttl_days(FileObject.Category.EXCHANGE)
    if ttl_days is None:
        return 0

    deadline = timezone.now() - timedelta(days=ttl_days)
    expired = ExchangeFile.objects.filter(uploaded_at__lte=deadline).select_related('file_object')

    purged = 0
    for exchange_file in expired:
        # Удаляем по одной записи, а не queryset.delete(): detach() выполняет
        # сигнал post_delete на конкретном экземпляре, и там же читается
        # пометка о причине удаления.
        attribute_deletion(exchange_file, consumer='exchange.ExchangeFile:expired')
        exchange_file.delete()
        purged += 1

    return purged


@shared_task(bind=True)
def import_exchange_archive(self, archive_object_id, owner_id, folder_id, user_id):
    """Распаковывает загруженный zip в личную папку сотрудника.

    Два разных пользователя в аргументах — это не описка: owner_id это чья
    папка (обменник открыт на запись всем, положить архив в чужую папку
    можно ровно так же, как отдельный файл), а user_id — кто загрузил.
    Ровно так же их различает UploadExchangeFileView.
    """
    from django.contrib.auth import get_user_model
    from storage.archives import extract_archive

    from .models import ExchangeFolder

    users = get_user_model().objects
    user = users.get(pk=user_id)
    owner = users.get(pk=owner_id)

    archive_object = FileObject.objects.get(pk=archive_object_id)
    root_folder = (
        ExchangeFolder.objects.get(pk=folder_id, owner=owner) if folder_id else None
    )

    def ensure_folder(parent, name):
        # owner обязателен: ExchangeFolder — не самостоятельная папка, а
        # вложенность внутри чьей-то личной (ARCHITECTURE.md, раздел 2).
        folder, _ = ExchangeFolder.objects.get_or_create(
            name=name, owner=owner, parent=parent, defaults={'created_by': user},
        )
        return folder

    def create_record(file_object, folder, name):
        ExchangeFile.objects.create(
            file_object=file_object, owner=owner, folder=folder, uploaded_by=user,
        )

    def on_progress(done, total):
        self.update_state(state='PROGRESS', meta={'done': done, 'total': total})

    try:
        result = extract_archive(
            archive_object.blob.file.path,
            user=user,
            category=FileObject.Category.EXCHANGE,
            root_folder=root_folder,
            ensure_folder=ensure_folder,
            create_record=create_record,
            on_progress=on_progress,
        )
    finally:
        # Архив был лишь способом донести файлы; в finally, потому что при
        # падении задачи он иначе остался бы ACTIVE без единой ссылки.
        StorageService.detach(
            archive_object, user=user, consumer='exchange.ExchangeFile:archive_import',
        )

    realtime.broadcast(
        realtime.SCOPE_EXCHANGE, realtime.exchange_location(owner_id, folder_id),
        action='files_created', actor=user, text='распаковал архив',
    )
    return result
