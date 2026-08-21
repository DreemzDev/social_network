"""Пределы хранения правятся в админке (storage.limits).

Главное, что здесь защищается: пределы читаются ПРИ КАЖДОМ вызове. Раньше
они были константами уровня модуля, и правка настройки подействовала бы
только после перезапуска Daphne — администратор менял бы квоту в админке и
видел старое поведение, не понимая, почему.

Второе — порядок источников: пока записи нет, работают значения
settings.py (на них опирается вся остальная сотня тестов), как только она
появилась, побеждает она.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from storage import limits
from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject, StorageLimits
from storage.services import StorageService

User = get_user_model()


class LimitsSourceTest(TestCase):
    """Откуда берётся значение: settings.py или запись из админки."""

    def test_settings_used_while_record_does_not_exist(self):
        self.assertFalse(StorageLimits.objects.exists())
        with override_settings(STORAGE_MAX_UPLOAD_SIZE=7 * limits.MB):
            self.assertEqual(limits.max_upload_size(), 7 * limits.MB)

    def test_record_wins_over_settings(self):
        StorageLimits.objects.create(max_upload_size_mb=3)
        with override_settings(STORAGE_MAX_UPLOAD_SIZE=7 * limits.MB):
            self.assertEqual(limits.max_upload_size(), 3 * limits.MB)

    def test_change_applies_without_process_restart(self):
        record = StorageLimits.load()
        record.user_quota_mb = 10
        record.save()
        self.assertEqual(limits.user_quota(), 10 * limits.MB)

        # Тот же процесс, тот же импортированный модуль — новое значение.
        record.user_quota_mb = 20
        record.save()
        self.assertEqual(limits.user_quota(), 20 * limits.MB)

    def test_empty_quota_means_no_quota(self):
        """Пустое nullable-поле — это «без ограничения», а не «взять из
        settings»: иначе снять квоту в админке было бы нечем."""
        StorageLimits.objects.create(user_quota_mb=None)
        with override_settings(STORAGE_USER_QUOTA=5 * limits.MB):
            self.assertIsNone(limits.user_quota())

    def test_category_ttl_from_record(self):
        StorageLimits.objects.create(chat_ttl_days=3, exchange_ttl_days=None)
        self.assertEqual(
            StorageService.get_category_ttl_days(FileObject.Category.CHAT), 3
        )
        self.assertIsNone(
            StorageService.get_category_ttl_days(FileObject.Category.EXCHANGE)
        )

    def test_category_without_admin_field_still_reads_settings(self):
        """У каталога и документов отдела поля в админке нет намеренно —
        срока хранения у них не бывает, и значение обязано остаться из
        settings, а не превратиться в None по отсутствию поля."""
        StorageLimits.objects.create(chat_ttl_days=3)
        with override_settings(STORAGE_CATEGORY_TTL={'catalog': 42}):
            self.assertEqual(
                StorageService.get_category_ttl_days(FileObject.Category.CATALOG), 42
            )


class LimitsAppliedOnUploadTest(TestCase):
    """Значения из админки действительно останавливают загрузку."""

    def setUp(self):
        self.user = User.objects.create_user(username='limits_user', password='x')

    def _upload(self, size, name='limits.pdf'):
        uploaded = SimpleUploadedFile(name, b'x' * size, content_type='application/pdf')
        return StorageService.upload(
            uploaded, user=self.user, category=FileObject.Category.CATALOG,
        )

    def test_max_upload_size_from_record_rejects_file(self):
        StorageLimits.objects.create(max_upload_size_mb=1)
        with self.assertRaises(FileTooLargeError):
            self._upload(limits.MB + 1)

    def test_quota_from_record_rejects_second_file(self):
        StorageLimits.objects.create(user_quota_mb=1)
        self._upload(600 * 1024, name='first.pdf')
        with self.assertRaises(QuotaExceededError):
            self._upload(600 * 1024, name='second.pdf')


class LimitsAdminTest(TestCase):

    def test_load_creates_single_record_with_defaults(self):
        first = StorageLimits.load()
        second = StorageLimits.load()

        self.assertEqual(first.pk, 1)
        self.assertEqual(second.pk, 1)
        self.assertEqual(StorageLimits.objects.count(), 1)
        self.assertEqual(first.max_upload_size_mb, 100)

    def test_record_cannot_be_deleted(self):
        """Удалить запись нельзя: без неё пределы молча вернулись бы к
        значениям settings.py, и администратор не понял бы, куда делись его
        числа. В админке кнопка удаления тоже убрана."""
        StorageLimits.load().delete()
        self.assertEqual(StorageLimits.objects.count(), 1)


class LimitsShownToUserTest(TestCase):
    """Числа в подписи модалки — из тех же пределов, что и проверка.

    Раньше пределы распаковки нигде не показывались, и узнать их можно было
    только упёршись в отказ. Подпись, набранная в шаблоне руками, разошлась
    бы с проверкой при первой же правке в админке.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='limits_view', password='pass12345')
        self.client.force_login(self.user)

    def test_catalog_page_shows_limits_from_admin_record(self):
        StorageLimits.objects.create(archive_max_files=17, max_upload_size_mb=3)

        response = self.client.get(reverse('catalog_root'))

        self.assertContains(response, '17 файлов')
        self.assertEqual(response.context['fm_max_upload_size'], 3 * limits.MB)
