"""Тесты на гонки в StorageService (ARCHITECTURE.md, раздел 5.3).

Требуют реальных блокировок БД (select_for_update), поэтому осмысленны
только на PostgreSQL — на SQLite select_for_update не блокирует другие
соединения, и эти тесты не находили бы ничего, кроме случайной удачи.

Используется TransactionTestCase, а не TestCase: обычный TestCase оборачивает
каждый тест в одну транзакцию, откатываемую в конце, поэтому параллельный
поток в отдельном соединении не увидел бы данных, созданных в setUp, и
блокировки select_for_update не имели бы смысла проверять.
"""

import threading
import time

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from datetime import timedelta

from storage.models import FileBlob, FileObject
from storage.services import StorageService

User = get_user_model()


class Barrier:
    """Простой барьер на N потоков — все ждут, пока каждый не дойдёт до
    вызова wait(), и только тогда продолжают одновременно. Нужен, чтобы
    гарантированно попасть в окно гонки, а не полагаться на то, что ОС
    случайно переключит потоки в нужный момент."""

    def __init__(self, count):
        self._event = threading.Event()
        self._count = count
        self._lock = threading.Lock()
        self._arrived = 0

    def wait(self):
        with self._lock:
            self._arrived += 1
            if self._arrived >= self._count:
                self._event.set()
        self._event.wait(timeout=5)


class ConcurrentUploadTest(TransactionTestCase):
    """Сценарий 1: два пользователя одновременно грузят один и тот же файл.
    Ожидание: FileBlob создаётся только один раз, второй upload() подхватывает
    существующую запись через обработку IntegrityError (ARCHITECTURE.md, 5.3)."""

    def setUp(self):
        self.user1 = User.objects.create_user(username='racer1', password='x')
        self.user2 = User.objects.create_user(username='racer2', password='x')

    def test_two_uploads_of_identical_content_create_one_blob(self):
        content = b'identical content for race test'
        results = {}
        errors = []
        barrier = Barrier(2)

        def upload(key, user):
            try:
                connection.close()  # каждый поток — своё соединение к БД
                barrier.wait()
                f = SimpleUploadedFile(f'{key}.txt', content, content_type='text/plain')
                results[key] = StorageService.upload(f, user=user, category=FileObject.Category.EXCHANGE)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=upload, args=('a', self.user1))
        t2 = threading.Thread(target=upload, args=('b', self.user2))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f'upload() не должен бросать исключения наружу: {errors}')
        self.assertEqual(len(results), 2)
        self.assertEqual(
            results['a'].blob_id, results['b'].blob_id,
            'Оба upload() одинакового содержимого должны получить один и тот же blob',
        )
        self.assertEqual(
            FileBlob.objects.filter(checksum=results['a'].blob.checksum).count(), 1,
            'На диске/в БД не должно быть дубликата blob для одинакового содержимого',
        )


class PurgeVsRestoreRaceTest(TransactionTestCase):
    """Сценарий 2: purge_expired_orphans() пытается удалить файл в тот же
    момент, когда кто-то повторно загружает то же содержимое (восстановление
    из ORPHAN). Ожидание: либо файл остаётся ACTIVE и доступен, либо (если
    purge успел раньше) upload() создаёт новый blob — но не оказывается в
    ситуации 'ACTIVE, но файла на диске нет' (ARCHITECTURE.md, 5.3)."""

    def setUp(self):
        self.user = User.objects.create_user(username='restorer', password='x')

    def test_purge_rechecks_status_under_lock_before_deleting(self):
        content = b'orphan restore race content'
        f = SimpleUploadedFile('orig.txt', content, content_type='text/plain')
        file_object = StorageService.upload(f, user=self.user, category=FileObject.Category.EXCHANGE)
        blob_id = file_object.blob_id
        checksum = file_object.blob.checksum

        # Переводим blob в ORPHAN с просроченной датой — как будто detach()
        # произошёл 8 дней назад.
        StorageService.detach(file_object, user=self.user, consumer='test')
        blob = FileBlob.objects.get(pk=blob_id)
        self.assertEqual(blob.status, FileBlob.Status.ORPHAN)
        blob.orphaned_at = timezone.now() - timedelta(days=8)
        blob.save(update_fields=['orphaned_at'])

        barrier = Barrier(2)
        errors = []
        restored = {}

        def do_purge():
            try:
                connection.close()
                barrier.wait()
                # Небольшая пауза — даём upload() шанс первым захватить
                # блокировку в реалистичной части случаев, не детерминируя
                # порядок жёстко (порядок в этой гонке оба варианта верны).
                StorageService.purge_expired_orphans()
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        def do_restore():
            try:
                connection.close()
                barrier.wait()
                f2 = SimpleUploadedFile('restored.txt', content, content_type='text/plain')
                restored['obj'] = StorageService.upload(f2, user=self.user, category=FileObject.Category.CHAT)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t_purge = threading.Thread(target=do_purge)
        t_restore = threading.Thread(target=do_restore)
        t_purge.start()
        t_restore.start()
        t_purge.join(timeout=10)
        t_restore.join(timeout=10)

        self.assertEqual(errors, [], f'Гонка не должна приводить к необработанным исключениям: {errors}')
        self.assertIn('obj', restored, 'upload() должен был успешно вернуть FileObject')

        result_blob = restored['obj'].blob
        result_blob.refresh_from_db()

        # Главная инвариант: если blob числится ACTIVE, файл физически должен
        # существовать на диске. Не должно быть комбинации "ACTIVE, но файла нет".
        if result_blob.status == FileBlob.Status.ACTIVE:
            self.assertTrue(
                result_blob.file.storage.exists(result_blob.file.name),
                'blob помечен ACTIVE, но файл физически отсутствует на диске — '
                'purge удалил его без учёта восстановления',
            )


class DetachVsNewReferenceRaceTest(TransactionTestCase):
    """Сценарий 3: detach() проверяет, остались ли ссылки на FileObject, в
    момент, когда создаётся новая ссылка на тот же blob (через повторную
    загрузку идентичного содержимого другим потребителем). Ожидание: blob не
    должен потерять единственную актуальную ссылку (ARCHITECTURE.md, 5.3)."""

    def setUp(self):
        self.user = User.objects.create_user(username='detacher', password='x')

    def test_detach_does_not_orphan_blob_with_concurrent_new_reference(self):
        content = b'detach vs new reference race content'
        f1 = SimpleUploadedFile('first.txt', content, content_type='text/plain')
        obj1 = StorageService.upload(f1, user=self.user, category=FileObject.Category.EXCHANGE)
        blob_id = obj1.blob_id

        barrier = Barrier(2)
        errors = []

        def do_detach():
            try:
                connection.close()
                barrier.wait()
                StorageService.detach(obj1, user=self.user, consumer='test')
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        def do_new_upload():
            try:
                connection.close()
                barrier.wait()
                f2 = SimpleUploadedFile('second.txt', content, content_type='text/plain')
                StorageService.upload(f2, user=self.user, category=FileObject.Category.CHAT)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=do_detach)
        t2 = threading.Thread(target=do_new_upload)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f'Гонка не должна приводить к необработанным исключениям: {errors}')

        blob = FileBlob.objects.get(pk=blob_id)
        remaining_refs = FileObject.objects.filter(blob=blob).count()

        # Инвариант: если есть хоть один FileObject на blob — blob обязан
        # быть ACTIVE (иначе через 7 дней purge удалит файл, который
        # используется).
        if remaining_refs > 0:
            self.assertEqual(
                blob.status, FileBlob.Status.ACTIVE,
                f'На blob осталось {remaining_refs} ссылок, но статус не ACTIVE — '
                f'файл будет удалён по расписанию, хотя используется',
            )
