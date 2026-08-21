from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from storage import limits, realtime
from storage.signals import attribute_deletion

from .models import DepartmentDocument, DepartmentFolder

@shared_task
def cleanup_deptdocs_trash():
    """Окончательно удаляет документы отдела, пролежавшие в корзине дольше
    срока хранения корзины (задаётся в админке). См. catalog.tasks.cleanup_catalog_trash —
    та же логика, отдельная задача, потому что модуль не должен трогать
    чужие таблицы."""
    deadline = timezone.now() - timedelta(days=limits.trash_retention_days())
    expired = DepartmentDocument.objects.filter(is_deleted=True, deleted_at__lte=deadline)

    purged = 0
    for document in expired:
        attribute_deletion(document, consumer='deptdocs.DepartmentDocument:trash_expired')
        document.delete()
        purged += 1

    return purged


@shared_task(bind=True)
def import_deptdocs_archive(self, archive_object_id, folder_id, user_id):
    """Распаковывает загруженный zip в папку приватного доступа.

    Отличие от каталога одно, но принципиальное: у папки есть список
    допущенных, и созданная из архива подпапка обязана унаследовать его от
    родителя. Папка с пустым allowed_users не видна НИКОМУ, включая того,
    кто её только что создал (DepartmentFolder.is_accessible_by), — то есть
    распакованные в неё документы просто исчезли бы из интерфейса.
    """
    from django.contrib.auth import get_user_model
    from storage.archives import extract_archive
    from storage.models import FileObject
    from storage.services import StorageService

    user = get_user_model().objects.get(pk=user_id)
    archive_object = FileObject.objects.get(pk=archive_object_id)
    root_folder = DepartmentFolder.objects.get(pk=folder_id)

    def ensure_folder(parent, name):
        folder, created = DepartmentFolder.objects.get_or_create(
            name=name, parent=parent, defaults={'created_by': user},
        )
        if created:
            folder.allowed_users.set(parent.allowed_users.all())
        return folder

    def create_record(file_object, folder, name):
        DepartmentDocument.objects.create(
            folder=folder, file_object=file_object, title=name, uploaded_by=user,
        )

    def on_progress(done, total):
        self.update_state(state='PROGRESS', meta={'done': done, 'total': total})

    try:
        result = extract_archive(
            archive_object.blob.file.path,
            user=user,
            category=FileObject.Category.DOCUMENT,
            root_folder=root_folder,
            ensure_folder=ensure_folder,
            create_record=create_record,
            on_progress=on_progress,
        )
    finally:
        # Архив был лишь способом донести файлы; в finally, потому что при
        # падении задачи он иначе остался бы ACTIVE без единой ссылки.
        StorageService.detach(
            archive_object, user=user, consumer='deptdocs.DepartmentDocument:archive_import',
        )

    realtime.broadcast(
        realtime.SCOPE_DEPTDOCS, realtime.folder_location(folder_id),
        action='files_created', actor=user, text='распаковал архив',
    )
    return result
