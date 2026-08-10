from django.conf import settings
from django.db import models

from storage.models import FileObject


class DepartmentFolder(models.Model):
    """Папка с ограниченным доступом — задаёт видимость и права,
    документы внутри наследуют её (см. DepartmentDocument.allowed_users,
    property). Дерево через self-FK, как catalog.CatalogFolder.

    Доступ даётся конкретным сотрудникам (allowed_users), а не отделам:
    любой сотрудник может создать папку и указать, кто ещё видит и
    управляет её содержимым. Все участники allowed_users равноправны —
    среди них нет отдельного статуса у создателя (created_by — только
    информационное поле "кто завёл папку"). Любой участник может менять
    список allowed_users, в том числе убрать из него самого себя."""

    name = models.CharField(max_length=255, verbose_name='Название')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children'
    )
    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='accessible_department_folders'
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Папка документов отдела'
        verbose_name_plural = 'Папки документов отдела'

    def __str__(self):
        return self.name

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse('deptdocs_folder', args=[self.pk])

    def get_download_url(self) -> str:
        """Скачать папку целиком архивом. Рядом с get_delete_url по той же
        причине: карточка папки общая на три модуля и адреса берёт с
        модели, а не собирает {% url %} с именем маршрута каждого."""
        from django.urls import reverse

        return reverse('deptdocs_folder_download', args=[self.pk])

    def get_delete_url(self) -> str:
        from django.urls import reverse

        return reverse('deptdocs_folder_delete', args=[self.pk])

    @property
    def access_note(self) -> str:
        """Подпись под карточкой папки вместо общего слова «папка».

        У приватного доступа главный вопрос к папке — кто её видит; без
        этого папки визуально неотличимы друг от друга и от папок
        каталога, хотя смысл у них принципиально разный."""
        count = self.allowed_users.count()
        return f'доступ: {count} чел.'

    def is_accessible_by(self, user) -> bool:
        return self.allowed_users.filter(pk=user.pk).exists()


class DepartmentDocument(models.Model):
    """Документ с ограниченным доступом — аналог папок с ограниченным
    доступом из старой сетевой структуры.

    Права наследуются от folder.allowed_users, а не задаются на самом
    документе: одна папка настраивается один раз, все файлы внутри
    автоматически получают тот же список допущенных. Документ вне папки
    (folder=None) не наследует ничьих прав и не виден никому.

    Права проверяются здесь, не в storage: folder.is_accessible_by(user) в
    DownloadDepartmentDocumentView (ARCHITECTURE.md, раздел 8)."""

    folder = models.ForeignKey(
        DepartmentFolder, on_delete=models.CASCADE, null=True, blank=True, related_name='documents'
    )
    file_object = models.ForeignKey(FileObject, on_delete=models.PROTECT, related_name='+')
    title = models.CharField(max_length=255, verbose_name='Название документа')

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title

    def is_accessible_by(self, user) -> bool:
        return self.folder_id is not None and self.folder.is_accessible_by(user)
