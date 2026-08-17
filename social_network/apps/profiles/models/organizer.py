"""Личный органайзер сотрудника: задачи, заметки, события календаря."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone


class Task(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Не начата'
        IN_PROGRESS = 'in_progress', 'В работе'
        DONE = 'done', 'Выполнена'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='tasks', verbose_name="Исполнитель")
    assigned_by = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name='assigned_tasks',
        null=True, blank=True, verbose_name="Постановщик"
    )
    title = models.CharField(max_length=255, verbose_name="Задача")
    description = models.TextField(blank=True, verbose_name="Описание")
    due_date = models.DateField(null=True, blank=True, verbose_name="Срок выполнения")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED, verbose_name="Статус")
    is_completed = models.BooleanField(default=False, verbose_name="Выполнено")
    is_edited = models.BooleanField(default=False, verbose_name="Изменена постановщиком после создания")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['is_completed', 'due_date', '-created']
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.is_completed = self.status == self.Status.DONE
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return bool(self.due_date and not self.is_completed and self.due_date < date.today())


class Note(models.Model):
    class Color(models.TextChoices):
        YELLOW = 'yellow', 'Жёлтый'
        PINK = 'pink', 'Розовый'
        BLUE = 'blue', 'Голубой'
        GREEN = 'green', 'Зелёный'
        PURPLE = 'purple', 'Фиолетовый'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='notes', verbose_name="Пользователь")
    content = models.TextField(blank=True, verbose_name="Текст заметки")
    color = models.CharField(max_length=10, choices=Color.choices, default=Color.YELLOW, verbose_name="Цвет")
    position = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    updated = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ['position', '-updated']
        verbose_name = 'Личная заметка'
        verbose_name_plural = 'Личные заметки'

    def __str__(self):
        return f"Заметка {self.user}"


class Event(models.Model):
    class EventType(models.TextChoices):
        PERSONAL = 'personal', 'Личное'
        CORPORATE = 'corporate', 'Корпоративное'

    class Recurrence(models.TextChoices):
        NONE = 'none', 'Не повторять'
        WEEKLY = 'weekly', 'Каждую неделю'
        MONTHLY = 'monthly', 'Каждый месяц'
        YEARLY = 'yearly', 'Каждый год'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='events', null=True, blank=True, verbose_name="Пользователь")
    created_by = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name='created_events',
        null=True, blank=True, verbose_name="Автор события"
    )
    share_group = models.UUIDField(null=True, blank=True, editable=False, verbose_name="Группа общего события")
    title = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.PERSONAL, verbose_name="Тип события")
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, default=Recurrence.NONE, verbose_name="Повтор")
    date = models.DateField(verbose_name="Дата")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    source_task = models.OneToOneField(
        'Task', on_delete=models.CASCADE, null=True, blank=True,
        related_name='calendar_event', verbose_name="Связанная задача"
    )

    class Meta:
        ordering = ['date']
        verbose_name = 'Событие календаря'
        verbose_name_plural = 'События календаря'

    def __str__(self):
        return f"{self.title} ({self.date})"
