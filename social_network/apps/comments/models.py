from django.db import models

from posts.models import Post

from django.contrib.auth import get_user_model

# Create your models here.
class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete = models.CASCADE, related_name="post_comments")
    comment_author = models.ForeignKey(get_user_model(), on_delete = models.CASCADE)
    comment_text = models.CharField('Текст комментария', max_length = 350)
    comment_pubdate = models.DateTimeField(verbose_name='Дата публикации', auto_now_add=True,)

    def __str__(self):
        return self.comment_text

    class Meta:
        verbose_name = 'Комментарий'
        verbose_name_plural = 'Комментарии'
    
        ordering = ['-comment_pubdate',] #Сортирока как на сайте так и в админке от новой новости к более старой