"""Опрос статуса фоновой задачи (/storage/task-status/<task_id>/).

Массовые операции (перемещение/удаление) уходят в Celery и возвращают
task_id вместо готового результата — фронт узнаёт, что происходит, только
через этот эндпоинт. Под CELERY_TASK_ALWAYS_EAGER=True (settings.py, только
для manage.py test) задача уже отработала синхронно к моменту запроса
статуса, поэтому здесь проверяется именно состояние SUCCESS."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService
from storage.utils import FM_TASK_HISTORY_LIMIT, FM_TASK_SESSION_KEY

User = get_user_model()


class TaskStatusViewTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='task_status_user', password='pass12345')
        self.client.force_login(self.user)

    def test_status_of_completed_bulk_trash_task(self):
        uploaded = SimpleUploadedFile('doc.pdf', b'content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(file_object=file_object, title='Документ', uploaded_by=self.user)

        bulk_response = self.client.post(reverse('catalog_bulk_trash'), {'doc_ids': [document.pk]})
        task_id = bulk_response.json()['task_id']

        status_response = self.client.get(reverse('storage_task_status', args=[task_id]))

        self.assertEqual(status_response.status_code, 200)
        payload = status_response.json()
        self.assertEqual(payload['state'], 'SUCCESS')
        self.assertEqual(payload['done'], 1)
        self.assertEqual(payload['total'], 1)

    def test_unknown_task_id_is_not_found(self):
        """Раньше здесь было 200 c PENDING: Celery считает неизвестный id
        просто «ещё не начатой задачей», и вьюха отдавала этот ответ кому
        угодно. Теперь эндпоинт отвечает только про задачи, запущенные этой
        же сессией, поэтому неизвестный id — 404."""
        response = self.client.get(
            reverse('storage_task_status', args=['00000000-0000-0000-0000-000000000000'])
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.json())

    def test_another_user_cannot_read_someone_elses_task(self):
        """Собственно дыра: task_id не секрет — он виден в трафике и в
        логах, а владения не проверялось вообще."""
        uploaded = SimpleUploadedFile('other.pdf', b'other content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Чужой документ', uploaded_by=self.user,
        )
        task_id = self.client.post(
            reverse('catalog_bulk_trash'), {'doc_ids': [document.pk]},
        ).json()['task_id']

        outsider = User.objects.create_user(username='task_status_outsider', password='pass12345')
        other_client = Client()
        other_client.force_login(outsider)

        response = other_client.get(reverse('storage_task_status', args=[task_id]))

        self.assertEqual(response.status_code, 404)

    def test_owner_keeps_access_after_launching_many_tasks(self):
        """История задач в сессии ограничена FM_TASK_HISTORY_LIMIT, чтобы
        сессия не росла бесконечно. Проверяется, что предел не режет по
        живому: последняя запущенная задача обязана остаться доступной."""
        uploaded = SimpleUploadedFile('limit.pdf', b'limit content', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.CATALOG)
        document = CatalogDocument.objects.create(
            file_object=file_object, title='Документ', uploaded_by=self.user,
        )

        task_id = None
        for _ in range(FM_TASK_HISTORY_LIMIT + 3):
            task_id = self.client.post(
                reverse('catalog_bulk_trash'), {'doc_ids': [document.pk]},
            ).json()['task_id']

        response = self.client.get(reverse('storage_task_status', args=[task_id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.session[FM_TASK_SESSION_KEY]), FM_TASK_HISTORY_LIMIT)
