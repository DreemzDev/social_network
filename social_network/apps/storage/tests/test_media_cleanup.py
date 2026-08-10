"""Файлы обычных ImageField не переживают свою запись в БД.

Django этого сам не делает, и на боевой БД к моменту написания тестов
накопилось 58 файлов (~43 МБ) при нуле ссылающихся на них записей: 17 в
галерее, 27 в изображениях постов и 14 аватаров. Аватары — отдельный случай:
их не удаляли, их **меняли**, и каждая смена оставляла прежний файл.

Почему это не через storage — см. `storage/mediafiles.py`: картинкам не
нужны ни права, ни TTL, ни дедупликация, а `FileBlob.file` этому механизму,
наоборот, противопоказан (на один файл ссылается несколько FileObject).

MEDIA_ROOT здесь свой на каждый тест, а не на прогон: тест проверяет
состояние диска, и общий каталог означал бы, что файл от соседнего теста
считается «своим» (SESSION_CONTEXT.md, «Тесты пишут медиа во временный
каталог»).
"""
import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from gallery.models import GalleryImage
from posts.models import Post, PostImage
from sitesettings.models import SiteSettings
from storage.mediafiles import find_untracked_media, registered_fields, upload_roots

User = get_user_model()

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
    b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
    b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class MediaCleanupTestCase(TestCase):
    """Общая обвязка: свой MEDIA_ROOT и выполнение on_commit-колбэков.

    `_delete_on_commit()` откладывает удаление до фиксации транзакции, а
    `TestCase` каждую фиксацию откатывает — без `captureOnCommitCallbacks`
    колбэк не выполнится никогда, и тест показывал бы «файл не удалён» там,
    где в бою он удаляется.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='portal-media-cleanup-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def path(self, field_file):
        return os.path.join(self.media_root, field_file.name.replace('/', os.sep))

    def image(self, name='pic.png'):
        return SimpleUploadedFile(name, PNG, content_type='image/png')


class DeletedRecordRemovesFileTest(MediaCleanupTestCase):

    def test_deleting_gallery_image_removes_file(self):
        picture = GalleryImage.objects.create(image=self.image('gallery.png'))
        path = self.path(picture.image)
        self.assertTrue(os.path.exists(path))

        with self.captureOnCommitCallbacks(execute=True):
            picture.delete()

        self.assertFalse(os.path.exists(path))

    def test_deleting_post_removes_files_of_its_images(self):
        """Каскад: изображения уносит FK, и сигнал приходит на каждую строку —
        отдельно удалять их вьюхе не нужно."""
        post = Post.objects.create(content='С фотографиями')
        first = PostImage.objects.create(post=post, image=self.image('one.png'))
        second = PostImage.objects.create(post=post, image=self.image('two.png'))
        paths = [self.path(first.image), self.path(second.image)]

        with self.captureOnCommitCallbacks(execute=True):
            post.delete()

        for path in paths:
            self.assertFalse(os.path.exists(path), path)

    def test_deleting_record_without_file_does_not_fail(self):
        """blank=True — обычное состояние: у поста может не быть картинки."""
        post = Post.objects.create(content='Без фотографий')

        with self.captureOnCommitCallbacks(execute=True):
            post.delete()

        self.assertFalse(Post.objects.filter(pk=post.pk).exists())


class ReplacedFileIsRemovedTest(MediaCleanupTestCase):

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='media_avatar', password='pass12345')

    def test_replacing_avatar_removes_the_previous_file(self):
        self.user.avatar = self.image('old.png')
        self.user.save()
        old_path = self.path(self.user.avatar)

        with self.captureOnCommitCallbacks(execute=True):
            self.user.avatar = self.image('new.png')
            self.user.save()

        new_path = self.path(self.user.avatar)
        self.assertNotEqual(old_path, new_path)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(new_path))

    def test_saving_other_fields_keeps_the_avatar(self):
        """UserActivityMiddleware зовёт save(update_fields=['last_activity'])
        на каждый запрос. Если бы замена определялась без оглядки на
        update_fields, аватар пережил бы ровно один переход по порталу."""
        self.user.avatar = self.image('keep.png')
        self.user.save()
        path = self.path(self.user.avatar)

        with self.captureOnCommitCallbacks(execute=True):
            self.user.last_name = 'Иванов'
            self.user.save(update_fields=['last_name'])

        self.assertTrue(os.path.exists(path))

    def test_saving_the_same_file_keeps_it(self):
        """Форма настроек присылает модель целиком, и обычное сохранение
        профиля не должно снести аватар, который никто не менял."""
        self.user.avatar = self.image('same.png')
        self.user.save()
        path = self.path(self.user.avatar)

        with self.captureOnCommitCallbacks(execute=True):
            self.user.cab = '204'
            self.user.save()

        self.assertTrue(os.path.exists(path))

    def test_replacing_site_logo_removes_the_previous_file(self):
        """Запись настроек одна и не удаляется — файлы за ней остаются
        только при замене."""
        site = SiteSettings.load()
        site.logo = self.image('logo-old.png')
        site.save()
        old_path = self.path(site.logo)

        with self.captureOnCommitCallbacks(execute=True):
            site.logo = self.image('logo-new.png')
            site.save()

        self.assertFalse(os.path.exists(old_path))


class DeletionWaitsForCommitTest(MediaCleanupTestCase):

    def test_file_survives_until_the_transaction_commits(self):
        """Откат вернёт запись, а файл вернуть неоткуда. Лишний файл на диске
        чинится media_verify, потерянный из-под живой записи — ничем."""
        picture = GalleryImage.objects.create(image=self.image('pending.png'))
        path = self.path(picture.image)

        with self.captureOnCommitCallbacks(execute=False):
            picture.delete()
            self.assertTrue(os.path.exists(path))

        self.assertTrue(os.path.exists(path))


class RegistryTest(TestCase):

    def test_blob_file_is_not_registered(self):
        """FileBlob.file живёт по другим правилам: на один файл ссылается
        несколько FileObject, физическое удаление — отложенное, через ORPHAN
        (ARCHITECTURE.md, раздел 7). Попади он сюда — удаление одной ссылки
        снесло бы файл из-под остальных."""
        from storage.models import FileBlob

        self.assertNotIn(FileBlob, registered_fields())

    def test_expected_models_are_registered(self):
        registered = {model._meta.label: fields for model, fields in registered_fields().items()}

        self.assertEqual(registered.get('gallery.GalleryImage'), ('image',))
        self.assertEqual(registered.get('posts.PostImage'), ('image',))
        self.assertEqual(registered.get('posts.Post'), ('photo',))
        self.assertEqual(registered.get('profiles.User'), ('avatar', 'cover'))
        self.assertEqual(registered.get('sitesettings.SiteSettings'), ('logo',))

    def test_upload_roots_strip_the_date_part(self):
        """upload_to='photos/%Y/%m/%d/' — каталогом владеет 'photos', иначе
        сверка искала бы файлы в несуществующем пути с процентами."""
        roots = upload_roots()

        self.assertIn('photos', roots)
        self.assertIn('gallery', roots)
        self.assertIn('avatar', roots)
        self.assertNotIn('storage', roots)


class MediaVerifyTest(MediaCleanupTestCase):

    def test_file_without_record_is_reported_and_deleted(self):
        orphan = os.path.join(self.media_root, 'gallery', 'orphan.png')
        os.makedirs(os.path.dirname(orphan), exist_ok=True)
        with open(orphan, 'wb') as handle:
            handle.write(PNG)

        self.assertIn(orphan, find_untracked_media())

        call_command('media_verify', '--delete-untracked')

        self.assertFalse(os.path.exists(orphan))

    def test_file_with_record_is_left_alone(self):
        picture = GalleryImage.objects.create(image=self.image('tracked.png'))
        path = self.path(picture.image)

        call_command('media_verify', '--delete-untracked')

        self.assertTrue(os.path.exists(path))

    def test_report_without_flag_deletes_nothing(self):
        orphan = os.path.join(self.media_root, 'avatar', 'orphan.png')
        os.makedirs(os.path.dirname(orphan), exist_ok=True)
        with open(orphan, 'wb') as handle:
            handle.write(PNG)

        call_command('media_verify')

        self.assertTrue(os.path.exists(orphan))

    def test_storage_blobs_are_out_of_scope(self):
        """У blob'ов своя сверка (storage_verify) и свои правила: файл без
        FileObject там — законный ORPHAN, ждущий срока, а не мусор."""
        blob = os.path.join(self.media_root, 'storage', 'blobs', 'ab', 'deadbeef')
        os.makedirs(os.path.dirname(blob), exist_ok=True)
        with open(blob, 'wb') as handle:
            handle.write(b'blob')

        call_command('media_verify', '--delete-untracked')

        self.assertTrue(os.path.exists(blob))
