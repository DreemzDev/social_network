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