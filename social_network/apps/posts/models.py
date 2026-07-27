

import datetime
from django.db import models
from django.urls import reverse
from django.contrib.auth.models import User

from django.contrib.auth import get_user_model


# Create your models here.
class Post(models.Model):

    content = models.TextField(blank=True, verbose_name="Текст поста")   #Поле может быть пустым blank=True
    photo = models.ImageField(upload_to="photos/%Y/%m/%d/", blank=True, null=True, verbose_name="Изображение (устаревшее поле)")
    time_create = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")
    time_update = models.DateTimeField(auto_now=True, verbose_name="Время изменения")
    author = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, related_name='posts', null=True, default=None, verbose_name="Создатель поста")
    likes = models.ManyToManyField(get_user_model(), related_name='liked_posts', blank=True)
    viewers = models.ManyToManyField(get_user_model(), related_name='viewed_posts', blank=True)

    
    def __str__(self):
        return self.content   


    def get_absolute_url(self):
        return reverse('post', kwargs={'post_id': self.pk}) #Позволяет формировать нужный нам маршрут для постов, для каждого поста форимруется путь post/id.pk (в данном случае мы берем атрибут pk)

    class Meta:
        verbose_name = 'Все новости организации'
        verbose_name_plural = 'Все новости организации'
        ordering = ['-time_create',] #Сортирока как на сайте так и в админке от новой новости к более старой


class PostImage(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='images', verbose_name="Пост")
    image = models.ImageField(upload_to="photos/%Y/%m/%d/", verbose_name="Изображение")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = 'Изображение поста'
        verbose_name_plural = 'Изображения поста'
        ordering = ['order', 'id']

    def __str__(self):
        return f"Изображение поста #{self.post_id}"


class PostFile(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='files', verbose_name="Пост")
    file = models.FileField(upload_to="post_files/%Y/%m/%d/", verbose_name="Файл")
    original_name = models.CharField(max_length=255, blank=True, verbose_name="Исходное имя файла")

    class Meta:
        verbose_name = 'Файл поста'
        verbose_name_plural = 'Файлы поста'
        ordering = ['id']

    def __str__(self):
        return self.original_name or self.file.name

    def save(self, *args, **kwargs):
        if not self.original_name and self.file:
            self.original_name = self.file.name
        super().save(*args, **kwargs)

    @property
    def size_display(self):
        size = self.file.size
        for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == 'Б' else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} ТБ"

    @property
    def extension(self):
        return self.original_name.rsplit('.', 1)[-1].upper() if '.' in self.original_name else ''

