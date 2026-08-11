from django.db import models
from django.urls import reverse
from django.contrib.auth import get_user_model

from storage.images import process_on_save
from storage.models import FileObject


# Create your models here.
class Post(models.Model):

    content = models.TextField(blank=True, verbose_name="Текст поста")   #Поле может быть пустым blank=True
    # У 6 существующих постов есть данные в этом поле — нельзя удалить без
    # миграции данных (перенос в PostImage). Оставлено намеренно.
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
    # Миниатюра для ленты: в карточке поста снимок показывается размером с
    # ладонь, а грузился до этого целиком (ARCHITECTURE.md, 1.1 — картинки
    # остаются обычными ImageField, но сжатие им нужно).
    thumbnail = models.ImageField(
        upload_to="photos/%Y/%m/%d/thumbs/", blank=True, null=True, editable=False,
        verbose_name="Миниатюра",
    )
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = 'Изображение поста'
        verbose_name_plural = 'Изображения поста'
        ordering = ['order', 'id']

    def __str__(self):
        return f"Изображение поста #{self.post_id}"

    def save(self, *args, **kwargs):
        # Только новый файл: без этой оговорки смена порядка картинок в
        # посте пересжимала бы уже сжатое, теряя качество на каждом круге
        # (см. storage.images.process_on_save).
        process_on_save(self, 'image', thumbnail_field='thumbnail')
        super().save(*args, **kwargs)

    @property
    def preview_url(self) -> str:
        """Миниатюра для ленты, оригинал — по клику (lightbox)."""
        if self.thumbnail:
            return self.thumbnail.url
        return self.image.url if self.image else ''


class PostFile(models.Model):
    """Вложенный документ поста — хранится через storage (не собственный
    FileField), т.к. пересылаемые в постах файлы (приказы, бланки) часто
    дублируются с информационным каталогом и обменником; дедупликация между
    модулями даёт реальную экономию (ARCHITECTURE.md, раздел 1.1)."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='files', verbose_name="Пост")
    file_object = models.ForeignKey(FileObject, on_delete=models.PROTECT, related_name='+', verbose_name="Файл")

    class Meta:
        verbose_name = 'Файл поста'
        verbose_name_plural = 'Файлы поста'
        ordering = ['id']

    def __str__(self):
        return self.file_object.original_name

    @property
    def original_name(self):
        return self.file_object.original_name

    @property
    def size_display(self):
        size = self.file_object.size
        for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == 'Б' else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} ТБ"

    @property
    def extension(self):
        return self.file_object.extension

