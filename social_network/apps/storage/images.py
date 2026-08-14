"""Сжатие изображений и миниатюры для обычных `ImageField`.

Уменьшение до `IMAGE_MAX_SIDE`, пересжатие, миниатюра для сетки, снятие EXIF.
Анимация и прозрачность не трогаются; если обработка не дала выигрыша,
остаётся оригинал. Файлы storage не затрагиваются — документы обязаны
доезжать байт в байт.

Обоснование и цифры — SESSION_CONTEXT.md, «Сжатие изображений и миниатюры».
"""
import io
import logging
import os

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# Форматы с анимацией — их обработка сводится к порче файла.
ANIMATED_FORMATS = {'GIF'}


def _max_side() -> int:
    return getattr(settings, 'IMAGE_MAX_SIDE', 1920)


def _quality() -> int:
    return getattr(settings, 'IMAGE_QUALITY', 85)


def _thumbnail_side() -> int:
    return getattr(settings, 'IMAGE_THUMBNAIL_SIDE', 480)


def prepare(django_file):
    """(сжатое | None, миниатюра | None) за одно чтение файла.

    None вместо сжатого — «оставить оригинал»: анимация, нечитаемый файл или
    отсутствие выигрыша.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        django_file.open()
        original_bytes = django_file.read()
    except Exception:
        logger.warning('Не удалось прочитать изображение %s', django_file.name, exc_info=True)
        return None, None

    try:
        with Image.open(io.BytesIO(original_bytes)) as image:
            source_format = (image.format or '').upper()
            # is_animated есть не у всех форматов, отсюда getattr.
            if source_format in ANIMATED_FORMATS or getattr(image, 'is_animated', False):
                return None, None

            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in ('RGBA', 'LA') or (
                image.mode == 'P' and 'transparency' in image.info
            )
            target_format = 'PNG' if has_alpha else 'JPEG'
            image = image.convert('RGBA' if has_alpha else 'RGB')

            compressed = _encode_resized(
                image, _max_side(), target_format, django_file.name, len(original_bytes),
            )
            thumbnail = _encode_resized(
                image, _thumbnail_side(), target_format, django_file.name, None, suffix='_thumb',
            )
    except (UnidentifiedImageError, OSError, ValueError):
        logger.warning('Не удалось обработать изображение %s', django_file.name, exc_info=True)
        return None, None

    return compressed, thumbnail


def _encode_resized(image, max_side, image_format, original_name, original_size, suffix=''):
    """Копия, вписанная в квадрат max_side, готовая к записи.

    original_size задан — это основной файл, менять его есть смысл только
    если стало меньше.
    """
    from PIL import Image

    if max(image.size) <= max_side:
        # Оригинал и так мал: пересжатие — потеря качества, миниатюра крупнее
        # оригинала не нужна (шаблон покажет оригинал через preview_url).
        if original_size is not None or suffix:
            return None

    resized = image.copy()
    resized.thumbnail((max_side, max_side), Image.LANCZOS)

    buffer = io.BytesIO()
    if image_format == 'JPEG':
        resized.save(buffer, 'JPEG', quality=_quality(), optimize=True, progressive=True)
    else:
        resized.save(buffer, 'PNG', optimize=True)

    data = buffer.getvalue()
    if original_size is not None and len(data) >= original_size:
        return None

    return ContentFile(data, name=_target_name(original_name, image_format, suffix))


def _target_name(original_name, image_format, suffix) -> str:
    """Имя с расширением под фактический формат: иначе PNG, пересжатый в
    JPEG, остался бы `.png` с чужим содержимым внутри."""
    base = os.path.splitext(os.path.basename(original_name or 'image'))[0]
    extension = 'jpg' if image_format == 'JPEG' else 'png'
    return f'{base}{suffix}.{extension}'


def process_on_save(instance, field_name, thumbnail_field=None):
    """Сжать только что загруженный файл и собрать миниатюру.

    Зовётся из `save()` модели: путей загрузки несколько (галерея, форма
    поста, админка). `_committed` отсекает уже лежащие на диске файлы — иначе
    каждое сохранение записи пересжимало бы сжатое, теряя качество.
    """
    field_file = getattr(instance, field_name, None)
    if not field_file or getattr(field_file, '_committed', True):
        return

    compressed, thumbnail = prepare(field_file)

    if compressed is not None:
        field_file.save(compressed.name, compressed, save=False)

    if thumbnail_field and thumbnail is not None:
        getattr(instance, thumbnail_field).save(thumbnail.name, thumbnail, save=False)
