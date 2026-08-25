from django.templatetags.static import static
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import ListView, View
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.core.cache import cache

from django_private_chat2.models import MessageModel, DialogsModel

from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from ..models import Category, MessageAttachment, MessageReaction, MessageReply, looks_like_image
from ._common import clear_user_cache, push_chat_event

ALLOWED_REACTION_EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '😡']

# Сколько файлов принимается за одно сообщение. Ограничение не про место
# (его сторожит квота), а про сам запрос: сотня файлов одним POST заняла бы
# воркер Daphne на всё время загрузки.
MAX_ATTACHMENTS_PER_MESSAGE = 10

CONSUMER = 'profiles.MessageAttachment'


class DialogsWithUnreadMixin:
    """Строит список диалогов текущего пользователя с непрочитанными и последним сообщением."""

    def _get_dialogs_with_unread(self, query='', dept=''):
        dialogs = DialogsModel.objects.filter(
            Q(user1=self.request.user) | Q(user2=self.request.user)
        ).select_related('user1', 'user2', 'user1__cat', 'user2__cat')

        dialog_list = []
        for dialog in dialogs:
            other_user = dialog.user1 if dialog.user2 == self.request.user else dialog.user2

            if query and query.lower() not in other_user.get_full_name().lower() and query.lower() not in other_user.username.lower():
                continue
            if dept and (not other_user.cat or other_user.cat.name != dept):
                continue

            dialog.unread_count = MessageModel.objects.filter(
                sender=other_user, recipient=self.request.user, read=False
            ).count()
            dialog.other_user = other_user
            dialog.last_message = MessageModel.objects.filter(
                Q(sender=self.request.user, recipient=other_user) |
                Q(sender=other_user, recipient=self.request.user)
            ).select_related('sender').prefetch_related('attachments').order_by('-created').first()

            dialog_list.append(dialog)

        # Диалоги без единого сообщения (last_message is None) уходят в
        # конец списка; остальные — по времени последнего сообщения, от
        # новых к старым.
        dialog_list.sort(
            key=lambda d: (d.last_message is None, -d.last_message.created.timestamp() if d.last_message else 0)
        )
        return dialog_list


class DialogsListView(DialogsWithUnreadMixin, LoginRequiredMixin, ListView):
    template_name = 'profiles/dialogs.html'
    context_object_name = 'dialogs'
    paginate_by = 20

    def get_queryset(self):
        # DialogsModel не хранит текст/непрочитанные — фильтрация и
        # сортировка (по времени последнего сообщения) неизбежно идут в
        # Python, поэтому paginate_by режет уже собранный и отсортированный
        # список, а не queryset.
        return self._get_dialogs_with_unread(
            query=self.request.GET.get('q', ''),
            dept=self.request.GET.get('dept', ''),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()

        def qs_with(**overrides):
            params = self.request.GET.copy()
            params.pop('page', None)
            for key, value in overrides.items():
                if value:
                    params[key] = value
                else:
                    params.pop(key, None)
            return params.urlencode()

        context['dept_links'] = [
            {'label': 'Все отделы', 'qs': qs_with(dept=None), 'active': not self.request.GET.get('dept')},
            *[
                {
                    'label': cat.name,
                    'qs': qs_with(dept=cat.name),
                    'active': self.request.GET.get('dept') == cat.name,
                }
                for cat in context['cats']
            ],
        ]
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(
                'includes/chat/list_fragment.html', context, request=self.request
            )
            return JsonResponse({
                'html': html,
                'has_next': context['page_obj'].has_next() if context['page_obj'] else False,
            })
        return super().render_to_response(context, **response_kwargs)


def chat_ttl_days():
    return StorageService.get_category_ttl_days(FileObject.Category.CHAT)


def serialize_attachment(attachment, ttl_days=None):
    """Одна форма вложения на все пути: первая отрисовка страницы, догрузка
    истории и живое событие получателю.

    ttl_days передаётся снаружи, а не спрашивается здесь: сериализация идёт
    в цикле по сообщениям, и запрос за сроком на каждое вложение был бы
    лишним."""
    return {
        'id': attachment.pk,
        'name': attachment.original_name,
        'size': attachment.size_display,
        'extension': attachment.extension,
        'color': attachment.badge_color,
        'label': attachment.badge_label,
        'label_size': attachment.badge_font_size,
        'is_image': attachment.is_image and not attachment.is_expired,
        'expired': attachment.is_expired,
        'retention': attachment.retention_note(ttl_days),
        'url': None if attachment.is_expired else reverse('chat_attachment', args=[attachment.pk]),
    }


def _serialize_attachments(msg):
    attachments = list(msg.attachments.all())  # уже в prefetch
    if not attachments:
        return []
    ttl_days = chat_ttl_days()
    return [serialize_attachment(a, ttl_days) for a in attachments]


def _serialize_message(msg):
    """Общий формат JSON-представления сообщения для LoadMoreMessagesView и
    SendMessageWithReplyView — чтобы фронтенд получал одинаковую форму
    данных независимо от источника."""
    reply_info = getattr(msg, 'reply_info', None)
    reply_payload = None
    if reply_info:
        reply_payload = {
            'id': reply_info.reply_to_id,
            'text': reply_info.reply_to.text,
            'sender_name': reply_info.reply_to.sender.get_full_name() or reply_info.reply_to.sender.username,
        }

    reactions_by_emoji = {}
    for reaction in msg.reactions.all():
        reactions_by_emoji.setdefault(reaction.emoji, []).append({
            'avatar': reaction.user.avatar.url if reaction.user.avatar else static('img/avatar7.png'),
            'username': reaction.user.username,
        })
    reactions_payload = [
        {'emoji': emoji, 'count': len(users), 'users': users[:3]}
        for emoji, users in sorted(reactions_by_emoji.items(), key=lambda kv: -len(kv[1]))
    ]

    return {
        'id': msg.id,
        'text': msg.text,
        'sender_id': msg.sender.id,
        'sender_name': f"{msg.sender.last_name} {msg.sender.first_name}",
        'sender_avatar': msg.sender.avatar.url if msg.sender.avatar else None,
        'created': msg.created.strftime('%H:%M'),
        'file': msg.file.url if hasattr(msg, 'file') and msg.file else None,
        'attachments': _serialize_attachments(msg),
        'reply_to': reply_payload,
        'reactions': reactions_payload,
    }


class DialogMessagesView(DialogsWithUnreadMixin, LoginRequiredMixin, ListView):
    template_name = 'profiles/messages.html'
    context_object_name = 'messages'
    paginate_by = 30

    def get_queryset(self):
        self.other_user = get_object_or_404(get_user_model(), pk=self.kwargs['user_id'])

        # Сообщения помечаются прочитанными отдельным запросом с фронтенда
        # (MarkMessagesReadView), а не здесь — чтобы страница диалога
        # отрисовывалась сразу, не дожидаясь записи в БД.
        return MessageModel.objects.filter(
            Q(sender=self.request.user, recipient=self.other_user) |
            Q(sender=self.other_user, recipient=self.request.user)
        ).select_related('sender', 'recipient').prefetch_related(
            'reactions', 'reply_info__reply_to__sender', 'attachments',
        ).order_by('-created')[:self.paginate_by]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['other_user'] = self.other_user
        context['dialogs'] = self._get_dialogs_with_unread()
        context['active_user_id'] = self.other_user.pk
        context['messages'] = list(reversed(context['messages']))  # от старых к новым
        context['reaction_emojis'] = ALLOWED_REACTION_EMOJIS
        # Срок берётся из политики storage, а не пишется в шаблоне числом:
        # его правят в админке, и подпись обязана меняться вместе с ним.
        context['chat_ttl_days'] = chat_ttl_days()
        context['max_attachments'] = MAX_ATTACHMENTS_PER_MESSAGE
        return context


class LoadMoreMessagesView(LoginRequiredMixin, View):
    """API endpoint для ленивой подгрузки сообщений постранично вверх."""

    def get(self, request, user_id):
        other_user = get_object_or_404(get_user_model(), pk=user_id)
        before_id = request.GET.get('before_id')
        limit = 30

        queryset = MessageModel.objects.filter(
            Q(sender=request.user, recipient=other_user) |
            Q(sender=other_user, recipient=request.user)
        )
        if before_id:
            # Курсор по id самого старого уже отрисованного сообщения —
            # в отличие от числового offset, не зависит от того, сколько
            # новых сообщений пришло в диалог за это время.
            queryset = queryset.filter(id__lt=before_id)

        # Берём на одно сообщение больше лимита, чтобы узнать has_more без
        # отдельного COUNT()-запроса по всему диалогу на каждый скролл.
        messages = list(queryset.select_related('sender', 'recipient').prefetch_related(
            'reactions__user', 'reply_info__reply_to__sender', 'attachments',
        ).order_by('-created')[:limit + 1])

        has_more = len(messages) > limit
        messages = messages[:limit]

        return JsonResponse({
            'messages': [_serialize_message(msg) for msg in reversed(messages)],
            'has_more': has_more,
        })


class GetUnreadCountView(LoginRequiredMixin, View):
    """API endpoint для получения количества непрочитанных сообщений."""

    def get(self, request):
        cache_key_total = f'unread_count_{request.user.pk}'
        cache_key_dialogs = f'dialogs_unread_{request.user.pk}'

        total_unread = cache.get(cache_key_total)
        dialogs_unread = cache.get(cache_key_dialogs)

        if total_unread is None or dialogs_unread is None:
            total_unread = MessageModel.objects.filter(recipient=request.user, read=False).count()

            dialogs = DialogsModel.objects.filter(
                Q(user1=request.user) | Q(user2=request.user)
            ).select_related('user1', 'user2')
            other_user_ids = [
                (dialog.user1 if dialog.user2 == request.user else dialog.user2).pk
                for dialog in dialogs
            ]

            dialogs_unread = {}
            if other_user_ids:
                unread_counts = MessageModel.objects.filter(
                    sender_id__in=other_user_ids, recipient=request.user, read=False
                ).values('sender_id').annotate(count=Count('id'))
                dialogs_unread = {str(item['sender_id']): item['count'] for item in unread_counts}

            cache.set(cache_key_total, total_unread, 30)
            cache.set(cache_key_dialogs, dialogs_unread, 30)

        return JsonResponse({'total_unread': total_unread, 'dialogs_unread': dialogs_unread})


class SendMessageWithReplyView(LoginRequiredMixin, View):
    """Единственный путь отправки сообщения (обычного и ответа). AJAX,
    всегда возвращает JSON и пушит сообщение получателю через тот же
    Channels-канал, что и остальные события чата — потому что сама
    библиотека django_private_chat2 не даёт провести reply_to через
    собственный WebSocket-путь отправки без переопределения её валидатора."""

    def post(self, request, user_id):
        recipient = get_object_or_404(get_user_model(), pk=user_id)
        text = request.POST.get('text', '').strip()
        reply_to_id = request.POST.get('reply_to_id')
        uploads = request.FILES.getlist('files')

        if not text and not uploads:
            return JsonResponse({'success': False, 'error': 'Текст не может быть пустым'}, status=400)

        if len(uploads) > MAX_ATTACHMENTS_PER_MESSAGE:
            return JsonResponse(
                {'success': False,
                 'error': f'За раз можно отправить не больше {MAX_ATTACHMENTS_PER_MESSAGE} файлов'},
                status=400,
            )

        # Файлы принимаются ДО создания сообщения: упрись загрузка в квоту
        # на третьем файле — и в переписке осталось бы сообщение с частью
        # вложений, а отправитель увидел бы только текст ошибки.
        try:
            uploaded_objects = self._store_files(uploads)
        except (FileTooLargeError, QuotaExceededError) as error:
            return JsonResponse({'success': False, 'error': str(error)}, status=400)

        reply_to = None
        if reply_to_id:
            # reply_to обязан быть сообщением именно из этого диалога, а не
            # любым чужим сообщением текущего пользователя из другого
            # диалога — иначе можно процитировать чужой текст в другой диалог.
            reply_to = MessageModel.objects.filter(pk=reply_to_id).filter(
                Q(sender=request.user, recipient=recipient) | Q(sender=recipient, recipient=request.user)
            ).first()

        message = MessageModel.objects.create(sender=request.user, recipient=recipient, text=text)
        attachments = [
            MessageAttachment.objects.create(
                message=message,
                file_object=file_object,
                original_name=file_object.original_name,
                size=file_object.size,
                is_image=looks_like_image(file_object.mime_type),
            )
            for file_object in uploaded_objects
        ]
        DialogsModel.create_if_not_exists(request.user, recipient)
        clear_user_cache(recipient.pk)

        reply_payload = None
        if reply_to:
            MessageReply.objects.create(message=message, reply_to=reply_to)
            reply_payload = {
                'id': reply_to.id,
                'text': reply_to.text,
                'sender_name': reply_to.sender.get_full_name() or reply_to.sender.username,
                'is_own': reply_to.sender_id == request.user.pk,
            }

        payload = {
            'id': message.id,
            'text': message.text,
            'sender': str(request.user.pk),
            'receiver': str(recipient.pk),
            'created': message.created.strftime('%H:%M'),
            'reply_to': reply_payload,
            'attachments': [
                serialize_attachment(a, chat_ttl_days()) for a in attachments
            ],
        }
        push_chat_event(recipient.pk, 'new_reply_message', **payload)

        return JsonResponse({'success': True, **payload})

    def _store_files(self, uploads):
        """Кладёт файлы в storage, откатывая уже загруженные при отказе.

        Без отката файл, успевший загрузиться до отказа на следующем,
        остался бы в хранилище ACTIVE без единой ссылки — то есть занимал бы
        место и место в квоте отправителя навсегда.
        """
        stored = []
        try:
            for uploaded in uploads:
                stored.append(StorageService.upload(
                    uploaded, user=self.request.user, category=FileObject.Category.CHAT,
                ))
        except (FileTooLargeError, QuotaExceededError):
            for file_object in stored:
                StorageService.detach(file_object, user=self.request.user, consumer=CONSUMER)
            raise
        return stored


class MarkMessagesReadView(LoginRequiredMixin, View):
    """API endpoint для пометки сообщений как прочитанных."""

    def post(self, request, user_id):
        other_user = get_object_or_404(get_user_model(), pk=user_id)

        updated_count = MessageModel.objects.filter(
            sender=other_user, recipient=request.user, read=False
        ).update(read=True)

        if updated_count > 0:
            clear_user_cache(request.user.pk)
            push_chat_event(other_user.pk, 'message_read', message_id=0,
                             sender=str(request.user.pk), receiver=str(other_user.pk))

        return JsonResponse({'success': True, 'marked_read': updated_count})


class EditMessageView(LoginRequiredMixin, View):
    """API endpoint для редактирования собственного сообщения."""

    def post(self, request, message_id):
        message = get_object_or_404(MessageModel, pk=message_id, sender=request.user)
        text = request.POST.get('text', '').strip()

        if not text:
            return JsonResponse({'success': False, 'error': 'Текст не может быть пустым'}, status=400)

        message.text = text
        message.save(update_fields=['text'])
        message.refresh_from_db(fields=['text'])

        push_chat_event(message.recipient_id, 'message_edited', message_id=message.id, text=message.text,
                         sender=str(request.user.pk), receiver=str(message.recipient_id))

        # Сообщения, отвечающие на это, показывают его текст как цитату —
        # без этого события их цитаты остаются с устаревшим текстом до
        # перезагрузки страницы у получателя.
        if message.replies.exists():
            push_chat_event(message.recipient_id, 'reply_quote_updated',
                             reply_to_id=message.id, text=message.text, receiver=str(message.recipient_id))

        return JsonResponse({'success': True, 'text': message.text})


class DeleteMessageView(LoginRequiredMixin, View):
    """API endpoint для удаления собственного сообщения."""

    def post(self, request, message_id):
        message = get_object_or_404(MessageModel, pk=message_id, sender=request.user)
        recipient_id = message.recipient_id

        # Вложения удаляются явно: MessageModel из django_private_chat2 —
        # SoftDeletableModel, её delete() только ставит признак, каскада не
        # происходит, и файл остался бы в хранилище до истечения срока —
        # хотя пользователь его уже отозвал. Пометка нужна, чтобы в журнале
        # storage было видно, кто удалил.
        for attachment in message.attachments.all():
            attribute_deletion(attachment, user=request.user, consumer=CONSUMER)
            attachment.delete()

        message.delete()

        push_chat_event(recipient_id, 'message_deleted', message_id=message_id,
                         sender=str(request.user.pk), receiver=str(recipient_id))

        return JsonResponse({'success': True})


class ToggleMessageReactionView(LoginRequiredMixin, View):
    """API endpoint для установки/снятия реакции на сообщение (своё или собеседника)."""

    def post(self, request, message_id):
        message = get_object_or_404(
            MessageModel.objects.filter(Q(sender=request.user) | Q(recipient=request.user)),
            pk=message_id
        )
        emoji = request.POST.get('emoji', '')

        if emoji not in ALLOWED_REACTION_EMOJIS:
            return JsonResponse({'success': False, 'error': 'Недопустимый эмодзи'}, status=400)

        existing = MessageReaction.objects.filter(message=message, user=request.user).first()
        if existing and existing.emoji == emoji:
            existing.delete()
        else:
            # update_or_create — атомарная операция на уровне БД, в отличие
            # от отдельных .filter().first() + .create()/.save(), которые
            # могут столкнуться в гонке при двойном быстром клике
            # (IntegrityError на unique_together('message', 'user')).
            MessageReaction.objects.update_or_create(
                message=message, user=request.user, defaults={'emoji': emoji},
            )

        reactions_qs = MessageReaction.objects.filter(message=message).select_related('user').order_by('emoji', 'created')
        reactions_by_emoji = {}
        for reaction in reactions_qs:
            reactions_by_emoji.setdefault(reaction.emoji, []).append({
                'avatar': reaction.user.avatar.url if reaction.user.avatar else static('img/avatar7.png'),
                'username': reaction.user.username,
            })
        reactions = [
            {'emoji': emoji, 'count': len(users), 'users': users[:3]}
            for emoji, users in sorted(reactions_by_emoji.items(), key=lambda kv: -len(kv[1]))
        ]

        other_user_id = message.recipient_id if message.sender_id == request.user.pk else message.sender_id
        push_chat_event(other_user_id, 'message_reaction', message_id=message.id, reactions=reactions,
                         sender=str(request.user.pk), receiver=str(other_user_id))

        return JsonResponse({'success': True, 'reactions': reactions})


class ChatAttachmentView(LoginRequiredMixin, View):
    """Отдаёт вложение личного сообщения — только его собеседникам.

    storage прав не знает, их проверяет потребитель (ARCHITECTURE.md,
    раздел 8): переписка личная, и адрес вложения не должен открываться
    никому, кроме двоих участников диалога.
    """

    def get(self, request, attachment_id):
        attachment = get_object_or_404(
            MessageAttachment.objects.select_related('message', 'file_object__blob'),
            pk=attachment_id,
        )
        message = attachment.message
        if request.user.pk not in (message.sender_id, message.recipient_id):
            raise Http404

        if attachment.is_expired:
            raise Http404

        return StorageService.get_download_response(
            attachment.file_object, request, inline=request.GET.get('inline') == '1',
        )
