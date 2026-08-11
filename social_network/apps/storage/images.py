"""Сжатие изображений и миниатюры для обычных `ImageField`.

Зачем
-----
Фотографии приходят с телефонов и из мессенджеров как есть: на момент
написания самый тяжёлый файл в постах весил 8.1 МБ (PNG со скриншота), а
страница галереи грузила семнадцать полноразмерных снимков — пятнадцать
мегабайт на один заход. Хранение здесь вторично: главное — сколько
скачивает браузер.

Что делается и чего не делается
-------------------------------
«Без потери качества» в строгом смысле (lossless-оптимизация PNG) экономит
проценты и того не стоит. Реальный выигрыш дают три вещи, ни одна из
которых на портале не видна глазом:

1. **Уменьшение до разумной стороны** (`IMAGE_MAX_SIDE`). Снимок 4000 px
   шириной на странице всё равно показывается в колонке шириной 600.
2. **Пересжатие** (`IMAGE_QUALITY`). Меньшая сторона — уже меньший файл, а
   качество 85 от исходного отличает только пиксельное сравнение.
3. **Миниатюра** (`IMAGE_THUMBNAIL_SIDE`) для сетки. Это больше, чем первые
   два вместе: в сетке нужен снимок 480 px, а не оригинал, уменьшенный
   браузером.

Заодно снимается EXIF. Это не косметика: во-первых, `exif_transpose`
применяет ориентацию, без которой снимки с телефона лежат на боку;
во-вторых, EXIF несёт GPS-координаты и модель аппарата, а галерея видна
всем сотрудникам.

Границы
-------
- **Анимация не трогается.** Пересохранение GIF/анимированного WEBP
  оставило бы один кадр — молча испорченный файл хуже тяжёлого.
- **Прозрачность сохраняется**: изображение с альфа-каналом остаётся PNG.
  Перевод его в JPEG залил бы прозрачные области чёрным.
- **Если обработка не дала выигрыша, оригинал остаётся как есть** — незачем
  пересохранять файл ради того, чтобы он стал больше.
- Модуль про картинки вне storage (ARCHITECTURE.md, 1.1) и `FileBlob` не
  трогает: документы в storage обязаны доезжать до получателя байт в байт,
  их «оптимизировать» нельзя в принципе.
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
    """(сжатое изображение | None, миниатюра | None) за одно чтение файла.

    None на первом месте означает «оставить оригинал»: анимация, нечитаемый
    файл или случай, когда обработка только увеличила бы размер.
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
    """Копия изображения, вписанная в квадрат max_side, готовая к записи.

    original_size задан — значит это основной файл, и его есть смысл менять
    только если стало меньше. Для миниатюры сравнивать не с чем: она нужна
    всегда, пока исходник действительно больше неё.
    """
    from PIL import Image

    if max(image.size) <= max_side:
        if original_size is not None:
            # Картинка и так небольшая: пересжимать её ради нескольких
            # процентов — только терять качество.
            return None
        if suffix:
            # Миниатюра крупнее оригинала бессмысленна: шаблон в этом случае
            # покажет сам оригинал (preview_url).
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
    """Имя файла с расширением под фактический формат.

    Иначе после пересжатия PNG в JPEG на диске лежал бы `.png`, внутри
    которого JPEG: браузер это переживёт, а вот человек, скачавший файл,
    удивится.
    """
    base = os.path.splitext(os.path.basename(original_name or 'image'))[0]
    extension = 'jpg' if image_format == 'JPEG' else 'png'
    return f'{base}{suffix}.{extension}'


def process_on_save(instance, field_name, thumbnail_field=None):
    """Сжать только что загруженный файл и собрать миниатюру.

    Зовётся из `save()` модели, а не из вьюхи загрузки: путей загрузки
    несколько (страница галереи, форма поста, админка), и обработка,
    привязанная к одному из них, для остальных просто не выполнялась бы.

    Обрабатывается только **новый** файл. `_committed` — признак того, что
    файл ещё не записан на диск, то есть присвоен формой прямо сейчас; без
    этой проверки любое сохранение записи (смена порядка картинок в посте)
    пересжимало бы уже сжатое, теряя качество на каждом круге.
    """
    field_file = getattr(instance, field_name, None)
    if not field_file or getattr(field_file, '_committed', True):
        return

    compressed, thumbnail = prepare(field_file)

    if compressed is not None:
        field_file.save(compressed.name, compressed, save=False)

    if thumbnail_field and thumbnail is not None:
        getattr(instance, thumbnail_field).save(thumbnail.name, thumbnail, save=False)
