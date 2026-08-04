"""Опрос статуса фоновой задачи (/storage/task-status/<task_id>/).

Массовые операции (перемещение/удаление) уходят в Celery и возвращают
task_id вместо готового результата — фронт узнаёт, что происходит, только
через этот эндпоинт. Под CELERY_TASK_ALWAYS_EAGER=True (settings.py, только
для manage.py test) задача уже отработала синхронно к моменту запроса
статуса, поэтому здесь проверяется именно состояние SUCCESS."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService

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

    def test_status_of_unknown_task_id_is_pending(self):
        """Celery по умолчанию считает неизвестный id PENDING, а не 404 —
        так же ведёт себя AsyncResult для задачи, которая ещё не успела
        создать запись состояния."""
        response = self.client.get(reverse('storage_task_status', args=['00000000-0000-0000-0000-000000000000']))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state'], 'PENDING')
