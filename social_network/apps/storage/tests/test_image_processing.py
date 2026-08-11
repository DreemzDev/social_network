"""Сжатие изображений и миниатюры (storage/images.py).

Фотографии приходят с телефонов как есть: самый тяжёлый файл в постах на
момент написания весил 8.1 МБ, а страница галереи грузила 17 полноразмерных
снимков — 15 МБ за один заход.

Проверяется не «стало меньше байт» (это следствие), а решения, которые
легко потерять при правке: анимация не портится, прозрачность не заливается
чёрным, ориентация с телефона применяется, повторное сохранение записи не
пересжимает уже сжатое по кругу.

MEDIA_ROOT свой на каждый тест — тесты трогают диск (SESSION_CONTEXT.md).
"""
import io
import os
import random
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from gallery.models import GalleryImage
from posts.models import Post, PostImage
from storage.images import prepare


def photo_bytes(width, height, image_format='JPEG', mode='RGB', exif=None):
    """Картинка из случайных пикселей — как настоящая фотография.

    Не заливка и не градиент: и то, и другое PNG сжимает в несколько
    килобайт, и тогда пересжатие честно отказывается работать («меньше не
    стало — оставляем оригинал»), а тест выглядит как поломка. Данные
    детерминированные: seed фиксирован, значит и размеры воспроизводимы.
    """
    channels = 4 if mode == 'RGBA' else 3
    noise = random.Random(20260811).randbytes(width * height * channels)
    image = Image.frombytes(mode, (width, height), noise)

    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, image_format, exif=exif)
    else:
        image.save(buffer, image_format)
    return buffer.getvalue()


def animated_gif_bytes():
    frames = [Image.new('P', (40, 40), color=index) for index in (1, 2, 3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, 'GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buffer.getvalue()


class ImageProcessingTestCase(TestCase):

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='portal-images-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def upload(self, name, data, content_type='image/jpeg'):
        return SimpleUploadedFile(name, data, content_type=content_type)

    def size_on_disk(self, field_file):
        return os.path.getsize(os.path.join(self.media_root, field_file.name.replace('/', os.sep)))


@override_settings(IMAGE_MAX_SIDE=800, IMAGE_THUMBNAIL_SIDE=200)
class PrepareTest(ImageProcessingTestCase):

    def test_large_photo_is_downscaled(self):
        compressed, thumbnail = prepare(self.upload('big.jpg', photo_bytes(2000, 1500)))

        with Image.open(io.BytesIO(compressed.read())) as image:
            self.assertEqual(max(image.size), 800)
        with Image.open(io.BytesIO(thumbnail.read())) as image:
            self.assertEqual(max(image.size), 200)

    def test_large_photo_gets_smaller(self):
        source = photo_bytes(2000, 1500)

        compressed, _ = prepare(self.upload('big.jpg', source))

        self.assertLess(len(compressed.read()), len(source))

    def test_small_image_is_left_alone(self):
        """Пересжимать и без того маленькую картинку — только терять
        качество ради нескольких процентов."""
        compressed, thumbnail = prepare(self.upload('small.jpg', photo_bytes(150, 100)))

        self.assertIsNone(compressed)
        self.assertIsNone(thumbnail)

    def test_animation_is_not_touched(self):
        """Пересохранение оставило бы один кадр — молча испорченный файл
        хуже тяжёлого."""
        compressed, thumbnail = prepare(
            self.upload('anim.gif', animated_gif_bytes(), 'image/gif')
        )

        self.assertIsNone(compressed)
        self.assertIsNone(thumbnail)

    def test_transparency_survives(self):
        """Перевод PNG с альфа-каналом в JPEG залил бы прозрачное чёрным."""
        compressed, _ = prepare(
            self.upload('alpha.png', photo_bytes(2000, 1500, 'PNG', mode='RGBA'), 'image/png')
        )

        self.assertTrue(compressed.name.endswith('.png'))
        with Image.open(io.BytesIO(compressed.read())) as image:
            self.assertIn('A', image.getbands())

    def test_photo_without_alpha_becomes_jpeg(self):
        """Скриншот в PNG весит мегабайты; расширение меняется вместе с
        форматом, иначе на диске лежал бы .png с JPEG внутри."""
        compressed, _ = prepare(
            self.upload('shot.png', photo_bytes(2000, 1500, 'PNG'), 'image/png')
        )

        self.assertTrue(compressed.name.endswith('.jpg'))

    def test_exif_orientation_is_applied(self):
        """Снимок с телефона иначе лежит на боку: ориентация хранится в EXIF,
        который при пересохранении не переносится."""
        exif = Image.Exif()
        exif[0x0112] = 6  # повернуть на 90°
        portrait = photo_bytes(1200, 900, exif=exif)

        compressed, _ = prepare(self.upload('rotated.jpg', portrait))

        with Image.open(io.BytesIO(compressed.read())) as image:
            width, height = image.size
        self.assertGreater(height, width)

    def test_broken_file_keeps_the_original(self):
        """ImageField проверяет файл на форме, но сохранить запись можно и
        мимо неё — падать на save() из-за этого нельзя."""
        compressed, thumbnail = prepare(self.upload('broken.jpg', b'not an image at all'))

        self.assertIsNone(compressed)
        self.assertIsNone(thumbnail)


@override_settings(IMAGE_MAX_SIDE=800, IMAGE_THUMBNAIL_SIDE=200)
class GalleryImageProcessingTest(ImageProcessingTestCase):

    def test_uploaded_photo_is_compressed_and_gets_a_thumbnail(self):
        source = photo_bytes(2000, 1500)

        picture = GalleryImage.objects.create(image=self.upload('gal.jpg', source))

        self.assertTrue(picture.thumbnail)
        self.assertLess(self.size_on_disk(picture.image), len(source))
        self.assertLess(self.size_on_disk(picture.thumbnail), self.size_on_disk(picture.image))

    def test_grid_uses_the_thumbnail(self):
        picture = GalleryImage.objects.create(image=self.upload('gal.jpg', photo_bytes(2000, 1500)))

        self.assertEqual(picture.preview_url, picture.thumbnail.url)

    def test_preview_falls_back_to_the_original(self):
        """Снимки, загруженные до появления миниатюр, и те, что меньше
        миниатюры, должны показываться, а не исчезать."""
        picture = GalleryImage.objects.create(image=self.upload('tiny.jpg', photo_bytes(150, 100)))

        self.assertFalse(picture.thumbnail)
        self.assertEqual(picture.preview_url, picture.image.url)

    def test_resaving_does_not_recompress(self):
        """Иначе каждое сохранение записи прогоняло бы файл через JPEG
        заново, теряя качество на каждом круге."""
        picture = GalleryImage.objects.create(image=self.upload('gal.jpg', photo_bytes(2000, 1500)))
        name_before = picture.image.name
        size_before = self.size_on_disk(picture.image)

        picture.save()

        picture.refresh_from_db()
        self.assertEqual(picture.image.name, name_before)
        self.assertEqual(self.size_on_disk(picture.image), size_before)

    def test_deleting_removes_both_files(self):
        """Миниатюра — такой же файл на диске: она зарегистрирована в
        storage.mediafiles вместе с оригиналом."""
        picture = GalleryImage.objects.create(image=self.upload('gal.jpg', photo_bytes(2000, 1500)))
        paths = [
            os.path.join(self.media_root, picture.image.name.replace('/', os.sep)),
            os.path.join(self.media_root, picture.thumbnail.name.replace('/', os.sep)),
        ]

        with self.captureOnCommitCallbacks(execute=True):
            picture.delete()

        for path in paths:
            self.assertFalse(os.path.exists(path), path)


@override_settings(IMAGE_MAX_SIDE=800, IMAGE_THUMBNAIL_SIDE=200)
class PostImageProcessingTest(ImageProcessingTestCase):

    def test_post_image_is_compressed_and_gets_a_thumbnail(self):
        post = Post.objects.create(content='С фотографией')
        source = photo_bytes(2000, 1500)

        image = PostImage.objects.create(post=post, image=self.upload('post.jpg', source))

        self.assertTrue(image.thumbnail)
        self.assertLess(self.size_on_disk(image.image), len(source))

    def test_reordering_images_does_not_recompress(self):
        """Порядок картинок в посте меняется отдельным сохранением — файл при
        этом трогать нечего."""
        post = Post.objects.create(content='С фотографией')
        image = PostImage.objects.create(post=post, image=self.upload('post.jpg', photo_bytes(2000, 1500)))
        size_before = self.size_on_disk(image.image)

        image.order = 3
        image.save()

        self.assertEqual(self.size_on_disk(image.image), size_before)
