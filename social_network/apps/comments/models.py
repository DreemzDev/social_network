from django.db import models
from django.contrib.auth import get_user_model

from posts.models import Post


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="post_comments")
    comment_author = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    comment_text = models.CharField('Текст комментария', max_length=350)
    comment_pubdate = models.DateTimeField(verbose_name='Дата публикации', auto_now_add=True)
    comment_updated = models.DateTimeField(verbose_name='Дата изменения', auto_now=True)
    likes = models.ManyToManyField(get_user_model(), related_name='liked_comments', blank=True)

    def __str__(self):
        return self.comment_text

    @property
    def is_edited(self):
        # comment_updated проставляется через auto_now при каждом save(),
        # включая самый первый (создание) — если с тех пор прошла хотя бы
        # секунда, комментарий редактировали хотя бы раз.
        return (self.comment_updated - self.comment_pubdate).total_seconds() > 1

    class Meta:
        verbose_name = 'Комментарий'
        verbose_name_plural = 'Комментарии'
        ordering = ['-comment_pubdate']  # Сортировка как на сайте, так и в админке — от нового к старому