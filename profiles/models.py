from django.db import models
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
    
    avatar = models.ImageField(upload_to = 'images/avatar/', blank=True, verbose_name="Аватар")
    position = models.CharField( max_length=100, blank=True, verbose_name="Должность" )
    rank = models.CharField( max_length=100, blank=True, verbose_name="Ранг" )
    patronymic = models.CharField( max_length=100, blank=True, verbose_name="Отчество" )
    birth_date = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    phone = models.CharField( max_length=12, blank=True, verbose_name="Номер телефона" )
    online = models.BooleanField(default=False)
   

    def get_absolute_url(self):
      return reverse('profile', kwargs={'username': self.username})

    def __str__(self):
        return str(self.last_name)
    
  
    
    class Meta:
        verbose_name = 'Личный кабинет'
        verbose_name_plural = 'Личные кабинеты'

@receiver(user_logged_in)
def user_logged_in_callback(sender, request, user, **kwargs):
    user.online = True
    user.save()
 
@receiver(user_logged_out)
def user_logged_out_callback(sender, request, user, **kwargs):
    user.online = False
    user.save()

    