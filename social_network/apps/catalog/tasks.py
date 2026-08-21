from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from storage import limits, realtime
from storage.signals import attribute_deletion

from .models import CatalogDocument, CatalogFolder

@shared_task
def cleanup_catalog_trash():
    """Окончательно удаляет документы каталога, пролежавшие в корзине
    дольше срока хранения корзины (задаётся в админке).

    До этой задачи корзина каталога чистилась только вручную — документ,
    отправленный в корзину, оставался там навсегда, пока кто-то не заходил
    и не удалял его окончательно по одному. Обменник такую очистку уже имел
    (cleanup_expired_exchange_files), только там это очистка по TTL с
    момента загрузки, а не по сроку пребывания в корзине — здесь смысл
    другой: пользователь уже решил удалить файл, автоочистка лишь
    завершает то, что он начал.
    """
    deadline = timezone.now() - timedelta(days=limits.trash_retention_days())
    expired = CatalogDocument.objects.filter(is_deleted=True, deleted_at__lte=deadline)

    purged = 0
    for document in expired:
        # По одной записи, а не queryset.delete(): detach() выполняет сигнал
        # post_delete на конкретном экземпляре, и там же читается пометка о
        # причине удаления.
        attribute_deletion(document, consumer='catalog.CatalogDocument:trash_expired')
        document.delete()
        purged += 1

    return purged


@shared_task(bind=True)
def import_catalog_archive(self, archive_object_id, folder_id, user_id):
    """Распаковывает загруженный zip в папку каталога.

    Задача живёт здесь, а не в storage, по той же причине, что и очистка
    корзины выше: storage умеет разбирать архив и класть файлы, но не знает
    ни модели CatalogDocument, ни того, что папка каталога заводится без
    списка допущенных. Общая часть — storage.archives.extract_archive(),
    сюда попадает только знание о собственных таблицах.
    """
    from django.contrib.auth import get_user_model
    from storage.archives import extract_archive
    from storage.models import FileObject
    from storage.services import StorageService

    user = get_user_model().objects.get(pk=user_id)
    archive_object = FileObject.objects.get(pk=archive_object_id)
    root_folder = CatalogFolder.objects.get(pk=folder_id) if folder_id else None

    def ensure_folder(parent, name):
        # get_or_create, а не create: повторная загрузка того же архива
        # должна долить файлы в существующие папки, а не наплодить рядом
        # вторую «Приказы» с тем же именем.
        folder, _ = CatalogFolder.objects.get_or_create(
            name=name, parent=parent, defaults={'created_by': user},
        )
        return folder

    def create_record(file_object, folder, name):
        CatalogDocument.objects.create(
            folder=folder, file_object=file_object, title=name, uploaded_by=user,
        )

    def on_progress(done, total):
        self.update_state(state='PROGRESS', meta={'done': done, 'total': total})

    try:
        result = extract_archive(
            archive_object.blob.file.path,
            user=user,
            category=FileObject.Category.CATALOG,
            root_folder=root_folder,
            ensure_folder=ensure_folder,
            create_record=create_record,
            on_progress=on_progress,
        )
    finally:
        # Сам архив на портале не нужен — он был лишь способом донести
        # файлы. Отвязка в finally, а не после return: при падении задачи
        # архив иначе остался бы ACTIVE навсегда, без единой ссылки на
        # него, ровно как в разделе 5.5.
        StorageService.detach(
            archive_object, user=user, consumer='catalog.CatalogDocument:archive_import',
        )

    # Распаковка — такая же мутация папки, как загрузка файла, и обязана
    # доехать до тех, кто смотрит на неё сейчас. Инициатор обновит сетку
    # сам по завершении задачи, соседи — по этому событию.
    realtime.broadcast(
        realtime.SCOPE_CATALOG, realtime.folder_location(folder_id),
        action='files_created', actor=user, text='распаковал архив',
    )
    return result
