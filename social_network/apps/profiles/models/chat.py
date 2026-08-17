"""Расширения чата.

Сама `MessageModel` из django_private_chat2 не трогается — иначе пришлось
бы форкать миграции сторонней библиотеки.
"""
from django.contrib.auth import get_user_model
from django.db import models


class MessageReaction(models.Model):
    message = models.ForeignKey('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='reactions', verbose_name="Сообщение")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, verbose_name="Пользователь")
    emoji = models.CharField(max_length=8, verbose_name="Эмодзи")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")

    class Meta:
        unique_together = ('message', 'user')
        ordering = ('emoji',)
        verbose_name = 'Реакция на сообщение'
        verbose_name_plural = 'Реакции на сообщения'

    def __str__(self):
        return f"{self.user} {self.emoji} on message {self.message_id}"


class MessageReply(models.Model):
    """Связывает сообщение с тем, на которое оно отвечает. Отдельная
    модель-расширение (как MessageReaction) — сама MessageModel из
    django_private_chat2 не трогается, чтобы не форкать миграции сторонней
    библиотеки."""
    message = models.OneToOneField('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='reply_info', verbose_name="Сообщение-ответ")
    reply_to = models.ForeignKey('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='replies', verbose_name="Исходное сообщение")

    def __str__(self):
        return f"Message {self.message_id} replies to {self.reply_to_id}"
