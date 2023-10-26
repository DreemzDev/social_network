from django.db import models
from django.urls import reverse

# Create your models here.
class Category(models.Model):
   name = models.CharField(max_length=100, db_index=True, verbose_name="Категория") 

   def __str__(self):
      return self.name
   
   def get_absolute_url(self):
      return reverse('category', kwargs={'cat_id': self.pk})
   
   class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['id'] 