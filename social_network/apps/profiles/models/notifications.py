"""Уведомления портала — общие для всех модулей.

Их создаёт ; storage и его потребители
шлют через него же, своей модели уведомлений не заводят.
"""
from django.contrib.auth import get_user_model
from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        TASK_ASSIGNED = 'task_assigned', 'Поставлена задача'
        TASK_EDITED = 'task_edited', 'Задача изменена'
        TASK_STATUS_CHANGED = 'task_status_changed', 'Статус задачи изменён'
        POST_COMMENTED = 'post_commented', 'Новый комментарий к посту'
        FILE_SHARED = 'file_shared', 'Новый файл'

    recipient = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='notifications', verbose_name="Получатель")
    actor = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='+', null=True, blank=True, verbose_name="Инициатор")
    kind = models.CharField(max_length=30, choices=Kind.choices, verbose_name="Тип")
    text = models.CharField(max_length=255, verbose_name="Текст")
    task = models.ForeignKey('Task', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications', verbose_name="Задача")
    post = models.ForeignKey('posts.Post', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications', verbose_name="Пост")
    # Готовая ссылка вместо FK на конкретный объект — файловые уведомления
    # (FILE_SHARED) могут указывать на обменник, каталог или приватный
    # доступ, и заводить по FK на каждый из трёх модулей ради одной ссылки
    # избыточно: сама ссылка не участвует ни в какой бизнес-логике, кроме
    # перехода по клику.
    url = models.CharField(max_length=255, blank=True, verbose_name="Ссылка")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['-created']
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'

    def __str__(self):
        return self.text
