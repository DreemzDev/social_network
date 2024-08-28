from django.db import models

# Create your models here.
class GalleryImage(models.Model):
    image = models.ImageField(upload_to='gallery/', blank=True, null=True, verbose_name="Фотографии")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")

    def __str__(self):
        return str(self.image)