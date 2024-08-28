

import datetime
from django.db import models
from django.urls import reverse
from django.contrib.auth.models import User

from django.contrib.auth import get_user_model


# Create your models here.
class Post(models.Model):
    
    content = models.TextField(blank=True, verbose_name="Текст поста")   #Поле может быть пустым blank=True
    photo = models.ImageField(upload_to="photos/%Y/%m/%d/", blank=True, null=True, verbose_name="Изображение")
    time_create = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")
    time_update = models.DateTimeField(auto_now=True, verbose_name="Время изменения")
    author = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, related_name='posts', null=True, default=None, verbose_name="Создатель поста")
    likes = models.ManyToManyField(get_user_model(), related_name='likes')

   
    
    def __str__(self):
        return self.content   


    def get_absolute_url(self):
        return reverse('post', kwargs={'post_id': self.pk}) #Позволяет формировать нужный нам маршрут для постов, для каждого поста форимруется путь post/id.pk (в данном случае мы берем атрибут pk)

    class Meta:
        verbose_name = 'Все новости организации'
        verbose_name_plural = 'Все новости организации'
        ordering = ['-time_create',] #Сортирока как на сайте так и в админке от новой новости к более старой