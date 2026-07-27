from django.db import models
from category.models import Category
# from django.contrib.auth.models import User
from django.dispatch import receiver
from django.urls import reverse
from django.db.models.signals import post_save
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.auth.signals import user_logged_in, user_logged_out


# Create your models here.

class User(AbstractUser):
    
    avatar = models.ImageField(upload_to = 'avatar/', blank=True,null=True, verbose_name="Аватар")
    cover = models.ImageField(upload_to='cover/', blank=True, null=True, verbose_name="Обложка профиля")
    cat = models.ForeignKey(Category, on_delete=models.PROTECT, null=True,blank=True, verbose_name="Категории")
    position = models.CharField( max_length=100, blank=True, verbose_name="Должность" )
    rank = models.CharField( max_length=100, blank=True, verbose_name="Ранг" )
    patronymic = models.CharField( max_length=100, blank=True, verbose_name="Отчество" )
    birthday = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    cab = models.CharField(max_length=10, blank=True, verbose_name="Номер кабинета" )
    phone_pts = models.CharField(max_length=20, blank=True, verbose_name="Номер телефона 1" )
    phone_city = models.CharField(max_length=20, blank=True, verbose_name="Номер телефона 2" )
    phone_zs = models.CharField(max_length=20, blank=True, verbose_name="Номер телефона 3" )
    phone_9 = models.CharField(max_length=20, blank=True, verbose_name="Номер телефона 4" )
    phone_hc = models.CharField(max_length=20, blank=True, verbose_name="Номер телефона 5" )
    last_activity = models.DateTimeField(default=timezone.now, verbose_name="Последняя активность")
    security_answer = models.CharField(max_length=255, blank=True, verbose_name="Проверочное слово для сброса пароля")

    class EmployeeStatus(models.TextChoices):
        AT_WORK = 'at_work', 'На месте'
        SICK_LEAVE = 'sick_leave', 'Больничный'
        BUSINESS_TRIP = 'business_trip', 'Командировка'
        DO_NOT_DISTURB = 'do_not_disturb', 'Не беспокоить'

    employee_status = models.CharField(max_length=20, choices=EmployeeStatus.choices,
        default=EmployeeStatus.AT_WORK, verbose_name="Статус сотрудника")

    def get_absolute_url(self):
        return reverse('profile', kwargs={'username': self.username})

    @property
    def is_online(self):
        """Проверяет онлайн статус на основе последней активности"""
        if not self.last_activity:
            return False
        return (timezone.now() - self.last_activity) < timedelta(minutes=5)

    @property
    def online_status(self):
        """Возвращает детальный статус для отображения"""
        if not self.last_activity:
            return "offline"
            
        time_diff = timezone.now() - self.last_activity
        
        if time_diff < timedelta(minutes=5):
            return "online"
        elif time_diff < timedelta(minutes=15):
            return "recently"
        else:
            return "offline"

    def get_status_display(self):
        """Текстовое представление статуса"""
        status_map = {
            "online": "Онлайн",
            "recently": "Был(а) недавно", 
            "offline": "Офлайн"
        }
        return status_map.get(self.online_status, "Офлайн")

    def __str__(self):
        return f"{self.last_name} {self.first_name}"

    class Meta:
        verbose_name = 'Личный кабинет'
        verbose_name_plural = 'Личные кабинеты'

class MessageReaction(models.Model):
    message = models.ForeignKey('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='reactions', verbose_name="Сообщение")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, verbose_name="Пользователь")
    emoji = models.CharField(max_length=8, verbose_name="Эмодзи")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")

    class Meta:
        unique_together = ('message', 'user')
        ordering = ('emoji',)
        verbose_name = 'Реакция на сообщение'
        verbose_name_plural = 'Реакции на сообщения'

    def __str__(self):
        return f"{self.user} {self.emoji} on message {self.message_id}"


class Task(models.Model):
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='tasks', verbose_name="Пользователь")
    title = models.CharField(max_length=255, verbose_name="Задача")
    due_date = models.DateField(null=True, blank=True, verbose_name="Срок выполнения")
    is_completed = models.BooleanField(default=False, verbose_name="Выполнено")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['is_completed', 'due_date', '-created']
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'

    def __str__(self):
        return self.title


class Note(models.Model):
    user = models.OneToOneField(get_user_model(), on_delete=models.CASCADE, related_name='note', verbose_name="Пользователь")
    content = models.TextField(blank=True, verbose_name="Текст заметки")
    updated = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = 'Личная заметка'
        verbose_name_plural = 'Личные заметки'

    def __str__(self):
        return f"Заметка {self.user}"


class Event(models.Model):
    class EventType(models.TextChoices):
        PERSONAL = 'personal', 'Личное'
        CORPORATE = 'corporate', 'Корпоративное'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='events', null=True, blank=True, verbose_name="Пользователь")
    title = models.CharField(max_length=255, verbose_name="Название")
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.PERSONAL, verbose_name="Тип события")
    date = models.DateField(verbose_name="Дата")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['date']
        verbose_name = 'Событие календаря'
        verbose_name_plural = 'События календаря'

    def __str__(self):
        return f"{self.title} ({self.date})"


@receiver(user_logged_in)
def user_logged_in_callback(sender, request, user, **kwargs):
    """Обновляем время активности при входе"""
    user.last_activity = timezone.now()
    user.save(update_fields=['last_activity'])

@receiver(user_logged_out)
def user_logged_out_callback(sender, request, user, **kwargs):
    """Обновляем время активности при выходе"""
    user.last_activity = timezone.now()
    user.save(update_fields=['last_activity'])