from django.db import models


class SiteSettings(models.Model):
    """Единая запись с общими настройками сайта, редактируется через админку"""

    site_name = models.CharField(max_length=100, default='Название организации', verbose_name='Название организации')
    logo = models.ImageField(upload_to='sitesettings/', blank=True, null=True, verbose_name='Логотип')
    footer_text = models.CharField(max_length=255, blank=True, verbose_name='Текст в футере')

    class Meta:
        verbose_name = 'Настройки сайта'
        verbose_name_plural = 'Настройки сайта'

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
