from django.db import models

from storage.images import process_on_save


# Create your models here.
class GalleryImage(models.Model):
    image = models.ImageField(upload_to='gallery/', blank=True, null=True, verbose_name="Фотографии")
    # Миниатюра для сетки. Отдельным полем, а не генерацией на лету: сетка
    # галереи открывается чаще, чем в неё добавляют фото, и пересобирать
    # уменьшенную копию на каждый показ — работа впустую.
    thumbnail = models.ImageField(
        upload_to='gallery/thumbs/', blank=True, null=True, editable=False,
        verbose_name="Миниатюра",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")

    def __str__(self):
        return str(self.image)

    def save(self, *args, **kwargs):
        # В save(), а не во вьюхе загрузки: фотографии добавляют и со
        # страницы галереи, и из админки — обработка, привязанная к одному
        # пути, для второго просто не выполнялась бы.
        process_on_save(self, 'image', thumbnail_field='thumbnail')
        super().save(*args, **kwargs)

    @property
    def preview_url(self) -> str:
        """Что показывать в сетке: миниатюру, а если её нет — оригинал.

        Запасной вариант нужен для снимков, загруженных до появления
        миниатюр, и для тех, что меньше миниатюры и потому её не получили.
        """
        if self.thumbnail:
            return self.thumbnail.url
        return self.image.url if self.image else ''
