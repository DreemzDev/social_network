from django.db import models

# Create your models here.



class Phonebook(models.Model):
    title = models.CharField(max_length=255, verbose_name="Заголовок")
    book = models.FileField(upload_to="Phonebook/", blank=True, null=True, verbose_name="Справочники")

    def __str__(self):
        return self.title  
class Meta:
        verbose_name = 'Справочники'
        verbose_name_plural = 'Справочники'