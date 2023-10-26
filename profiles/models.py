from django.db import models
# from django.contrib.auth.models import User
from django.dispatch import receiver
from django.urls import reverse
from django.db.models.signals import post_save
from django.contrib.auth.models import AbstractUser
# Create your models here.

class User(AbstractUser):
    # user = models.OneToOneField(User, default=None, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to = 'images/avatar/', blank=True, verbose_name="Аватар")
    position = models.CharField( max_length=100, blank=True, verbose_name="Должность" )
    rank = models.CharField( max_length=100, blank=True, verbose_name="Ранг" )
    # last_name = models.CharField( max_length=100, blank=True, verbose_name="Фамилия" )
    # first_name= models.CharField( max_length=100, blank=True, verbose_name="Имя" )
    patronymic = models.CharField( max_length=100, blank=True, verbose_name="Отчество" )
    birth_date = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    phone = models.CharField( max_length=12, blank=True, verbose_name="Номер телефона" )
   

    def get_absolute_url(self):
      return reverse('profile', kwargs={'username': self.username})


    # @receiver(post_save, sender=User)
    # def create_user_profile(sender, instance, created, **kwargs):
    #     if created:
    #         Profile.objects.create(user=instance)

    # @receiver(post_save, sender=User)
    # def save_user_profile(sender, instance, **kwargs):
    #     instance.profile.save()
    
    # @receiver(post_save, sender=User)
    # def save_user_profile(sender, instance, **kwargs):
    #     instance.profile.save()
   
   
    def __str__(self):
        return str(self.rank)
    
  
    
    class Meta:
        verbose_name = 'Личный кабинет'
        verbose_name_plural = 'Личные кабинеты'