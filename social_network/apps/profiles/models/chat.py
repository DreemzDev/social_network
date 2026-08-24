"""Расширения чата.

Сама `MessageModel` из django_private_chat2 не трогается — иначе пришлось
бы форкать миграции сторонней библиотеки.
"""
from django.contrib.auth import get_user_model
from django.db import models

from storage.models import FileObject
from storage.utils import is_inline_safe


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


class MessageAttachment(models.Model):
    """Файл или фото, приложенные к личному сообщению.

    Своё расширение, а не `MessageModel.file` из django_private_chat2: там
    обычный FileField в общей медиатеке — файл раздаётся всем, кто знает
    адрес, не дедуплицируется и лежит вечно. Здесь storage: права проверяет
    вьюха, срок хранения задаётся в админке (по умолчанию 7 дней).

    Запись переживает истечение срока: file_object обнуляется, а имя и
    размер остаются, иначе на месте вложения оказывался бы пустой пузырь
    без объяснения, куда делся файл (profiles/tasks.py).
    """

    message = models.ForeignKey(
        'django_private_chat2.MessageModel', on_delete=models.CASCADE,
        related_name='attachments', verbose_name='Сообщение',
    )
    file_object = models.ForeignKey(
        FileObject, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        verbose_name='Файл',
    )
    original_name = models.CharField(max_length=255, verbose_name='Имя файла')
    size = models.PositiveBigIntegerField(default=0, verbose_name='Размер')
    is_image = models.BooleanField(default=False, verbose_name='Показывать картинкой')
    created = models.DateTimeField(auto_now_add=True)
    expired_at = models.DateTimeField(null=True, blank=True, verbose_name='Когда удалён по сроку')

    class Meta:
        ordering = ('id',)
        verbose_name = 'Вложение сообщения'
        verbose_name_plural = 'Вложения сообщений'

    def __str__(self):
        return self.original_name

    @property
    def is_expired(self) -> bool:
        return self.file_object_id is None

    @property
    def extension(self) -> str:
        _, dot, extension = (self.original_name or '').rpartition('.')
        return extension.upper()[:8] if dot else 'ФАЙЛ'

    @property
    def badge_color(self) -> str:
        """Цвет корешка на значке. По семейству формата, а не по каждому
        расширению: в переписке значок размером с ноготь, и различать в нём
        .xls от .xlsx незачем — важно с одного взгляда отличить таблицу от
        документа."""
        return BADGE_COLORS.get(self.extension, BADGE_COLORS[None])

    @property
    def size_display(self) -> str:
        size = float(self.size)
        for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
            if size < 1024:
                return f'{size:.0f} {unit}' if unit == 'Б' else f'{size:.1f} {unit}'
            size /= 1024
        return f'{size:.1f} ТБ'


# Цвета взяты у офисных пакетов, где они привычны: Word синий, Excel
# зелёный, PowerPoint оранжевый, PDF красный.
BADGE_COLORS = {
    'PDF': '#F15642',
    'DOC': '#2B579A', 'DOCX': '#2B579A', 'ODT': '#2B579A', 'RTF': '#2B579A',
    'XLS': '#217346', 'XLSX': '#217346', 'ODS': '#217346', 'CSV': '#217346',
    'PPT': '#D24726', 'PPTX': '#D24726', 'ODP': '#D24726',
    'ZIP': '#B7791F', 'RAR': '#B7791F', '7Z': '#B7791F',
    None: '#6B7280',
}


def looks_like_image(mime_type) -> bool:
    """Показывать ли вложение картинкой прямо в переписке.

    Не любой image/*: SVG — активный документ, и inline он выполняется в
    домене портала (storage/utils.py, INLINE_UNSAFE_MIME_TYPES).
    """
    return (mime_type or '').startswith('image/') and is_inline_safe(mime_type)
