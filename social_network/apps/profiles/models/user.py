"""Учётная запись сотрудника — AUTH_USER_MODEL проекта."""
from datetime import date, timedelta

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.core.cache import cache
from django.db import models
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

from .orgstructure import Category, Position, Rank


class User(AbstractUser):

    avatar = models.ImageField(upload_to = 'avatar/', blank=True,null=True, verbose_name="Аватар")
    cover = models.ImageField(upload_to='cover/', blank=True, null=True, verbose_name="Обложка профиля")
    cat = models.ForeignKey(Category, on_delete=models.PROTECT, null=True,blank=True, verbose_name="Категории")
    # SET_NULL, а не PROTECT: удаление должности из справочника — это
    # изменение структуры организации, а не повод запретить его из-за того,
    # что кто-то её занимает. Человек останется без должности, и это видно.
    position = models.ForeignKey(
        Position, on_delete=models.SET_NULL, null=True, blank=True, related_name='holders',
        verbose_name="Должность",
    )
    rank = models.ForeignKey(
        Rank, on_delete=models.SET_NULL, null=True, blank=True, related_name='holders',
        verbose_name="Звание",
    )
    patronymic = models.CharField( max_length=100, blank=True, verbose_name="Отчество" )
    birthday = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    cab = models.CharField(max_length=10, blank=True, verbose_name="Номер кабинета" )
    # Телефоны — в UserPhone: видов связи в организации больше, чем пять, и
    # шестой не должен стоить миграции и правки трёх шаблонов.
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
        # Маршрута 'profile' в проекте нет — страница профиля называется
        # 'addpost' (posts/urls.py). До разбора моделей метод просто ни разу
        # не вызывался и молча падал бы при первом же обращении.
        return reverse('addpost', kwargs={'username': self.username})

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

    _ONLINE_STATUS_LABELS = {
        "online": "Онлайн",
        "recently": "Был(а) недавно",
        "offline": "Офлайн",
    }

    def get_online_status_display(self):
        """Текстовое представление online_status.

        Не называется get_status_display() — у User нет поля status,
        и такое имя выглядело бы как автосгенерированный Django-метод
        для поля с choices (как get_status_display() у Task), но им не является."""
        return self._ONLINE_STATUS_LABELS.get(self.online_status, "Офлайн")

    def __str__(self):
        return f"{self.last_name} {self.first_name}"

    class Meta:
        verbose_name = 'Личный кабинет'
        verbose_name_plural = 'Личные кабинеты'


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
