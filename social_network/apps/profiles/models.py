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

    