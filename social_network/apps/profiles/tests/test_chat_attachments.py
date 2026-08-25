"""Файлы и фото в личной переписке.

Вложения пришлось делать своей моделью поверх django_private_chat2: её
собственный `MessageModel.file` — обычный FileField в общей медиатеке, то
есть файл доступен любому, кто знает адрес, не дедуплицируется и лежит
вечно. Здесь проверяется то, ради чего он заменён: доступ только двоим
участникам диалога и срок хранения, после которого файл действительно
исчезает с диска.

Отдельно проверяется отказ: сообщение не должно создаваться, если файл не
прошёл по размеру или квоте, — иначе в переписке оставался бы пузырь с
частью вложений, а отправитель видел бы только текст ошибки.
"""

import asyncio
import json
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from django_private_chat2.models import MessageModel

from profiles.consumers import ExtendedChatConsumer

from profiles.models import MessageAttachment
from profiles.tasks import cleanup_expired_chat_attachments
from storage.models import FileBlob, FileObject, StorageLimits
from storage.services import StorageService

User = get_user_model()

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
    b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
    b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class ChatAttachmentTestCase(TestCase):
    """Свой MEDIA_ROOT на каждый тест: вложения пишутся на диск по-настоящему."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='portal-chat-attachments-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.sender = User.objects.create_user(username='chat_sender', password='pass12345')
        self.recipient = User.objects.create_user(username='chat_recipient', password='pass12345')
        self.stranger = User.objects.create_user(username='chat_stranger', password='pass12345')
        self.client.force_login(self.sender)

    def send(self, text='', files=(), user=None):
        if user:
            self.client.force_login(user)
        payload = {'text': text}
        if files:
            payload['files'] = list(files)
        return self.client.post(
            reverse('send_message_reply', args=[self.recipient.pk]), payload
        )

    def document(self, name='Приказ.pdf', content=b'document body'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def photo(self, name='Фото.png'):
        return SimpleUploadedFile(name, PNG, content_type='image/png')


class SendingAttachmentsTest(ChatAttachmentTestCase):

    def test_file_is_sent_and_stored_in_chat_category(self):
        response = self.send(text='Держи приказ', files=[self.document()])

        self.assertEqual(response.status_code, 200)
        attachment = MessageAttachment.objects.get()
        self.assertEqual(attachment.original_name, 'Приказ.pdf')
        self.assertEqual(attachment.file_object.category, FileObject.Category.CHAT)
        self.assertEqual(attachment.message.sender, self.sender)
        self.assertFalse(attachment.is_image)

    def test_photo_is_marked_as_image(self):
        """Фото показывается прямо в переписке, документ — строкой со
        скачиванием; различие считается один раз при приёме."""
        self.send(files=[self.photo()])

        self.assertTrue(MessageAttachment.objects.get().is_image)

    def test_message_without_text_but_with_file_is_allowed(self):
        response = self.send(text='', files=[self.photo()])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(MessageModel.objects.count(), 1)

    def test_empty_message_is_still_refused(self):
        response = self.send(text='')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(MessageModel.objects.count(), 0)

    def test_too_many_files_are_refused_with_a_reason(self):
        response = self.send(files=[self.document(f'{i}.pdf', b'x') for i in range(11)])

        self.assertEqual(response.status_code, 400)
        self.assertIn('не больше', response.json()['error'])
        self.assertEqual(MessageModel.objects.count(), 0)

    def test_oversized_file_leaves_neither_message_nor_orphan(self):
        """Файлы принимаются до создания сообщения: упрись загрузка в
        предел на втором файле — и в переписке остался бы пузырь с одним
        вложением из двух, а первый файл занимал бы место без ссылок."""
        StorageLimits.objects.create(max_upload_size_mb=1)

        response = self.send(files=[
            self.document('первый.pdf', b'x' * 1024),
            self.document('второй.pdf', b'x' * (2 * 1024 * 1024)),
        ])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(MessageModel.objects.count(), 0)
        self.assertEqual(MessageAttachment.objects.count(), 0)
        self.assertFalse(
            FileBlob.objects.filter(status=FileBlob.Status.ACTIVE).exists(),
            'файл, загруженный до отказа, остался в хранилище со статусом ACTIVE',
        )

    def test_answer_carries_attachments_for_live_delivery(self):
        """Тот же список уходит получателю событием WebSocket — без него
        вложение появлялось бы у него только после перезагрузки страницы."""
        response = self.send(text='смотри', files=[self.photo()])

        payload = response.json()['attachments']
        self.assertEqual(len(payload), 1)
        self.assertTrue(payload[0]['is_image'])
        self.assertIn('/chat/attachment/', payload[0]['url'])


class AttachmentAccessTest(ChatAttachmentTestCase):

    def setUp(self):
        super().setUp()
        self.send(text='файл', files=[self.document()])
        self.attachment = MessageAttachment.objects.get()
        self.url = reverse('chat_attachment', args=[self.attachment.pk])

    def test_both_participants_can_open_it(self):
        for user in (self.sender, self.recipient):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_outsider_gets_404(self):
        """Переписка личная: storage прав не знает, и если их не проверить
        здесь, адрес вложения открывал бы файл любому сотруднику портала."""
        self.client.force_login(self.stranger)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])


class ChatAttachmentExpiryTest(ChatAttachmentTestCase):

    def _age(self, attachment, days):
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            created=timezone.now() - timedelta(days=days)
        )

    def test_attachment_older_than_ttl_loses_the_file(self):
        self.send(text='файл', files=[self.document()])
        attachment = MessageAttachment.objects.get()
        blob_id = attachment.file_object.blob_id
        self._age(attachment, 8)

        purged = cleanup_expired_chat_attachments()

        attachment.refresh_from_db()
        self.assertEqual(purged, 1)
        self.assertIsNone(attachment.file_object)
        self.assertIsNotNone(attachment.expired_at)
        self.assertEqual(
            FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN,
            'файл остался бы на диске навсегда — ровно то, ради чего задан срок',
        )

    def test_record_survives_so_the_bubble_can_explain(self):
        """Удалять саму запись нельзя: на месте вложения остался бы пустой
        пузырь без единого следа того, что файл вообще был."""
        self.send(text='файл', files=[self.document('Смета.pdf')])
        attachment = MessageAttachment.objects.get()
        self._age(attachment, 8)

        cleanup_expired_chat_attachments()

        attachment.refresh_from_db()
        self.assertEqual(attachment.original_name, 'Смета.pdf')
        self.assertTrue(attachment.is_expired)

    def test_fresh_attachment_is_not_touched(self):
        self.send(text='файл', files=[self.document()])
        attachment = MessageAttachment.objects.get()
        self._age(attachment, 3)

        self.assertEqual(cleanup_expired_chat_attachments(), 0)
        attachment.refresh_from_db()
        self.assertIsNotNone(attachment.file_object)

    def test_ttl_from_admin_record_is_respected(self):
        """Срок правится в админке; задача обязана спрашивать его каждый
        раз, а не запоминать при импорте модуля."""
        StorageLimits.objects.create(chat_ttl_days=1)
        self.send(text='файл', files=[self.document()])
        attachment = MessageAttachment.objects.get()
        self._age(attachment, 2)

        self.assertEqual(cleanup_expired_chat_attachments(), 1)

    def test_expired_attachment_is_no_longer_downloadable(self):
        self.send(text='файл', files=[self.document()])
        attachment = MessageAttachment.objects.get()
        self._age(attachment, 8)
        cleanup_expired_chat_attachments()

        response = self.client.get(reverse('chat_attachment', args=[attachment.pk]))

        self.assertEqual(response.status_code, 404)


class AttachmentLifecycleTest(ChatAttachmentTestCase):

    def test_deleting_message_releases_the_file(self):
        """MessageModel из django_private_chat2 — SoftDeletableModel: её
        delete() только ставит признак, каскада нет. Понадеявшись на него,
        мы оставили бы отозванный пользователем файл в хранилище до
        истечения срока — а он рассчитывал, что файла больше нет."""
        self.send(text='файл', files=[self.document()])
        attachment = MessageAttachment.objects.get()
        blob_id = attachment.file_object.blob_id

        self.client.post(reverse('delete_message', args=[attachment.message_id]))

        self.assertFalse(MessageAttachment.objects.exists())
        self.assertEqual(FileBlob.objects.get(pk=blob_id).status, FileBlob.Status.ORPHAN)

    def test_same_file_sent_twice_is_stored_once(self):
        self.send(text='раз', files=[self.document()])
        self.send(text='два', files=[self.document()])

        self.assertEqual(MessageAttachment.objects.count(), 2)
        self.assertEqual(FileBlob.objects.count(), 1, 'дедупликация storage не сработала')


class DialogPageRenderingTest(ChatAttachmentTestCase):

    def test_page_shows_attachment_and_retention_notice(self):
        self.send(text='файл', files=[self.document('Смета.pdf')])

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'Смета.pdf')
        self.assertContains(response, 'msg-attach-button')

    def test_document_card_shows_size_and_type(self):
        """В пузыре документ — карточка со значком, размером и типом, а
        скачивание отдельной кнопкой: раньше вся строка была ссылкой, и
        «посмотреть, что прислали» не отличалось от «скачать»."""
        self.send(text='смета', files=[self.document('Смета.pdf', b'x' * 2048)])

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'msg-attachment-file')
        self.assertContains(response, '2.0 КБ')
        self.assertContains(response, 'PDF')
        self.assertContains(response, 'download')

    def test_badge_carries_the_format_label(self):
        """Формат подписан прямо на корешке значка: цвет отличает семейство,
        а буквы — конкретный формат, и без них .docx от .odt не отличить."""
        self.send(files=[self.document('Смета.pdf'), self.document('Доклад.pptx')])

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))
        labels = {a.original_name: (a.badge_label, a.badge_font_size)
                  for a in MessageAttachment.objects.all()}

        self.assertEqual(labels['Смета.pdf'], ('PDF', '5'))
        self.assertEqual(labels['Доклад.pptx'], ('PPTX', '4'), 'четыре знака набираются мельче')
        self.assertContains(response, '>PDF</text>')
        self.assertContains(response, '>PPTX</text>')

    def test_badge_color_marks_the_family_of_the_format(self):
        """Цвет корешка — по семейству формата: в значке размером с ноготь
        важно с одного взгляда отличить таблицу от документа, а не .xls от
        .xlsx."""
        self.send(files=[
            self.document('Смета.xlsx'), self.document('Приказ.docx'), self.document('Скан.pdf'),
        ])

        colors = {a.extension: a.badge_color for a in MessageAttachment.objects.all()}

        self.assertEqual(colors['XLSX'], '#217346')
        self.assertEqual(colors['DOCX'], '#2B579A')
        self.assertEqual(colors['PDF'], '#F15642')

    def test_unknown_extension_still_gets_a_card(self):
        self.send(files=[self.document('без_расширения')])

        attachment = MessageAttachment.objects.get()

        self.assertEqual(attachment.extension, 'ФАЙЛ')
        self.assertEqual(attachment.badge_color, '#6B7280')

    def test_retention_is_written_on_the_card(self):
        """Срок стоит на самой карточке: получатель не нажимал «скрепку» и
        подсказки на ней не видел, а файл живёт считанные дни."""
        self.send(text='файл', files=[self.document('Смета.pdf')])

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'удалится через 7 дн.')

    def test_last_day_is_said_plainly(self):
        self.send(text='файл', files=[self.document('Смета.pdf')])
        attachment = MessageAttachment.objects.get()
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            created=timezone.now() - timedelta(days=7)
        )

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'удалится сегодня')

    def test_expired_attachment_is_explained_in_the_bubble(self):
        """После истечения срока остаётся та же карточка, только погасшая:
        видно, что за файл здесь был и что его удалили, а не пустой пузырь."""
        self.send(text='файл', files=[self.document('Смета.pdf')])
        attachment = MessageAttachment.objects.get()
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            created=timezone.now() - timedelta(days=8)
        )
        cleanup_expired_chat_attachments()

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'файл удалён')
        self.assertContains(response, 'Смета.pdf')
        self.assertContains(response, 'msg-attachment-file')
        self.assertNotContains(response, 'download title="Скачать «Смета.pdf»"')

    def test_expired_photo_becomes_a_card_too(self):
        """Картинку показывать нечем — вместо битой рамки та же карточка."""
        self.send(files=[self.photo('Фото.png')])
        attachment = MessageAttachment.objects.get()
        MessageAttachment.objects.filter(pk=attachment.pk).update(
            created=timezone.now() - timedelta(days=8)
        )
        cleanup_expired_chat_attachments()

        response = self.client.get(reverse('dialog_messages', args=[self.recipient.pk]))

        self.assertContains(response, 'msg-attachment-file')
        self.assertNotContains(response, '<img src="/chat/attachment/')


class AttachmentQuotaTest(ChatAttachmentTestCase):

    def test_quota_refusal_names_the_reason(self):
        """«Не удалось отправить» вместо «кончилась квота» оставляет
        пользователя без единого способа понять, что делать дальше."""
        StorageService.upload(
            SimpleUploadedFile('big.bin', b'x' * (900 * 1024), content_type='application/octet-stream'),
            user=self.sender, category=FileObject.Category.CHAT,
        )
        StorageLimits.objects.create(user_quota_mb=1)

        response = self.send(files=[self.document('ещё.pdf', b'x' * (500 * 1024))])

        self.assertEqual(response.status_code, 400)
        self.assertIn('квот', response.json()['error'])


class LiveDeliveryPayloadTest(SimpleTestCase):
    """Событие WebSocket обязано нести вложения.

    Регрессия, найденная только в браузере: HTTP-ответ отправителю вложения
    содержал, серверные тесты были зелёные, а consumer собирал полезную
    нагрузку получателя поле за полем и про attachments не знал — у
    собеседника сообщение приходило без файла, и увидеть вложение можно
    было только перезагрузив страницу.
    """

    def test_new_reply_message_includes_attachments(self):
        from django_private_chat2.models import MessageModel  # noqa: F401 (регистрация моделей)

        consumer = ExtendedChatConsumer()
        delivered = {}

        async def fake_send(text_data):
            delivered['payload'] = json.loads(text_data)

        consumer.send = fake_send
        asyncio.run(consumer.new_reply_message({
            'id': 1, 'text': 'вот файл', 'sender': '1', 'receiver': '2',
            'created': '12:00', 'reply_to': None,
            'attachments': [{'id': 5, 'name': 'Смета.pdf', 'is_image': False}],
        }))

        self.assertEqual(delivered['payload']['msg_type'], 104)
        self.assertEqual(delivered['payload']['attachments'][0]['name'], 'Смета.pdf')

    def test_event_without_attachments_still_delivers(self):
        """События приходят и из мест, которые про вложения не знают —
        KeyError здесь стоил бы получателю всего сообщения."""
        consumer = ExtendedChatConsumer()
        delivered = {}

        async def fake_send(text_data):
            delivered['payload'] = json.loads(text_data)

        consumer.send = fake_send
        asyncio.run(consumer.new_reply_message({
            'id': 2, 'text': 'просто текст', 'sender': '1', 'receiver': '2',
            'created': '12:01', 'reply_to': None,
        }))

        self.assertEqual(delivered['payload']['attachments'], [])
