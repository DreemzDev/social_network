"""Уведомления о новых файлах при загрузке в обменник и приватный доступ.

Каталог не уведомляет никого — он общедоступен, и рассылка при каждой
загрузке всем аутентифицированным была бы избыточным шумом. Обменник
уведомляет владельца личной папки (единственный получатель, папка = один
человек), приватный доступ — всех участников allowed_users кроме самого
загрузившего (папка равноправная, получателей несколько).
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from deptdocs.models import DepartmentFolder
from exchange.models import ExchangeFile
from profiles.models import Notification

User = get_user_model()


class ExchangeUploadNotificationTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username='notif_owner', password='pass12345')
        self.stranger = User.objects.create_user(username='notif_stranger', password='pass12345')

    def test_uploading_into_someone_elses_folder_notifies_the_owner(self):
        self.client.force_login(self.stranger)
        uploaded = SimpleUploadedFile('report.pdf', b'report content', content_type='application/pdf')

        self.client.post(reverse('exchange_upload', args=[self.owner.pk]), {'files': [uploaded]})

        notification = Notification.objects.filter(
            recipient=self.owner, kind=Notification.Kind.FILE_SHARED,
        ).first()
        self.assertIsNotNone(notification)
        self.assertEqual(notification.actor_id, self.stranger.pk)
        self.assertIn(reverse('exchange_folder', args=[self.owner.pk]), notification.url)

    def test_uploading_into_own_folder_does_not_notify_self(self):
        self.client.force_login(self.owner)
        uploaded = SimpleUploadedFile('own.pdf', b'own content', content_type='application/pdf')

        self.client.post(reverse('exchange_upload', args=[self.owner.pk]), {'files': [uploaded]})

        self.assertFalse(
            Notification.objects.filter(recipient=self.owner, kind=Notification.Kind.FILE_SHARED).exists()
        )


class DeptdocsUploadNotificationTest(TestCase):

    def setUp(self):
        self.uploader = User.objects.create_user(username='notif_uploader', password='pass12345')
        self.colleague = User.objects.create_user(username='notif_colleague', password='pass12345')
        self.folder = DepartmentFolder.objects.create(name='Отдел кадров', created_by=self.uploader)
        self.folder.allowed_users.add(self.uploader, self.colleague)
        self.client.force_login(self.uploader)

    def test_uploading_notifies_other_folder_members(self):
        uploaded = SimpleUploadedFile('order.pdf', b'order content', content_type='application/pdf')

        self.client.post(
            reverse('deptdocs_upload'), {'folder_id': self.folder.pk, 'file': uploaded},
        )

        notification = Notification.objects.filter(
            recipient=self.colleague, kind=Notification.Kind.FILE_SHARED,
        ).first()
        self.assertIsNotNone(notification)
        self.assertEqual(notification.actor_id, self.uploader.pk)

    def test_uploader_does_not_notify_self(self):
        uploaded = SimpleUploadedFile('order2.pdf', b'order content 2', content_type='application/pdf')

        self.client.post(
            reverse('deptdocs_upload'), {'folder_id': self.folder.pk, 'file': uploaded},
        )

        self.assertFalse(
            Notification.objects.filter(recipient=self.uploader, kind=Notification.Kind.FILE_SHARED).exists()
        )

    def test_member_without_access_is_not_notified(self):
        outsider = User.objects.create_user(username='notif_outsider', password='pass12345')
        uploaded = SimpleUploadedFile('order3.pdf', b'order content 3', content_type='application/pdf')

        self.client.post(
            reverse('deptdocs_upload'), {'folder_id': self.folder.pk, 'file': uploaded},
        )

        self.assertFalse(
            Notification.objects.filter(recipient=outsider, kind=Notification.Kind.FILE_SHARED).exists()
        )
