from typing import Any
from django.templatetags.static import static
from django.urls import reverse, reverse_lazy
from django.core.cache import cache
from django.db.models import Count, Q, Max
from django.utils import timezone
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import (
    DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
)
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin

from posts.models import Post
from posts.forms import AddPostForm
from posts.views import AddPost
from profiles.forms import AddProfileForm, SettingProfileForm, ChangePasswordForm, SecurityAnswerForm
from category.models import Category
from phonebook.models import Phonebook
from phonebook.forms import UpdateBookForm
from django_private_chat2.models import MessageModel, DialogsModel
from .models import MessageReaction, Task, Note, Event, Notification
from .forms import TaskForm, TaskEditForm, NoteForm, EventForm
from datetime import date, timedelta
from django.http import JsonResponse
from django.utils import timezone
from datetime import datetime
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import uuid


def notify(recipient, kind, text, actor=None, task=None):
    """Создаёт запись уведомления и пушит её получателю через тот же Channels-
    канал, что уже используется чатом (группа — str(user_id), см.
    apps/profiles/consumers.py:ExtendedChatConsumer.notification)."""
    notification = Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, text=text, task=task,
    )
    unread_count = Notification.objects.filter(recipient=recipient, is_read=False).count()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(str(recipient.id), {
        'type': 'notification',
        'notification': {
            'id': notification.id,
            'kind': notification.kind,
            'text': notification.text,
            'created': notification.created.isoformat(),
            'task_id': task.id if task else None,
        },
        'unread_count': unread_count,
    })
    return notification


class AddProfile(UpdateView):
    model = get_user_model()
    form_class = AddProfileForm
    template_name = 'profiles/addprofile.html'
    pk_url_kwarg = 'user_id'
    success_url = reverse_lazy('home')
    

class SettingProfile(UpdateView, DetailView):
    model = get_user_model()
    form_class = SettingProfileForm
    template_name = 'profiles/settingprofiles.html'
    pk_url_kwarg = 'user_id'

    def get_success_url(self):
        return reverse_lazy('addpost', kwargs={'username': self.object.username})

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('password_form', ChangePasswordForm(self.request.user))
        context.setdefault('security_form', SecurityAnswerForm(instance=self.request.user))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if 'change_password' in request.POST:
            password_form = ChangePasswordForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, request.user)
                return redirect(self.get_success_url())
            context = self.get_context_data(form=self.get_form_class()(instance=self.object))
            context['password_form'] = password_form
            return self.render_to_response(context)

        if 'change_security_answer' in request.POST:
            security_form = SecurityAnswerForm(request.POST, instance=self.object)
            if security_form.is_valid():
                security_form.save()
                return redirect(self.get_success_url())
            context = self.get_context_data(form=self.get_form_class()(instance=self.object))
            context['security_form'] = security_form
            return self.render_to_response(context)

        return super().post(request, *args, **kwargs)

class ShowUsers(ListView):
    model = get_user_model()
    template_name = 'profiles/all_users.html'
    # context_object_name = 'sh_users'
    
    def get_queryset(self): 
        query = self.request.GET.get('q')
        if not query :
            query = ""
        object_list = get_user_model().objects.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
        return object_list

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        # context["sh_online"] = get_user_model().objects.filter(online=True)
        context["cats"] = Category.objects.all()
        context['profile_user'] = get_user_model()
        context['user'] = get_user_model()
        
        return context


class ShowPhones(ListView, FormView):
    model = get_user_model()
    template_name = 'profiles/phones.html'
    form_class = UpdateBookForm
    success_url = reverse_lazy('show_phones')

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
    
    def get_queryset(self): 
        query = self.request.GET.get('q')
        if not query :
            query = ""
        object_list = get_user_model().objects.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
        return object_list
 
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cats"] = Category.objects.all()
        context["books"] = Phonebook.objects.all()
        return context

# ЧАТ И СООБЩЕНИЯ

def clear_user_cache(user_pk):
    """Централизованная очистка кеша пользователя"""
    cache.delete(f'unread_count_{user_pk}')
    cache.delete(f'dialogs_unread_{user_pk}')


class DialogsWithUnreadMixin:
    """Строит список диалогов текущего пользователя с непрочитанными и последним сообщением"""

    def _get_dialogs_with_unread(self):
        dialogs = DialogsModel.objects.filter(
            Q(user1=self.request.user) | Q(user2=self.request.user)
        ).select_related('user1', 'user2')

        dialog_list = []
        for dialog in dialogs:
            other_user = dialog.user1 if dialog.user2 == self.request.user else dialog.user2

            dialog.unread_count = MessageModel.objects.filter(
                sender=other_user,
                recipient=self.request.user,
                read=False
            ).count()

            dialog.other_user = other_user

            dialog.last_message = MessageModel.objects.filter(
                Q(sender=self.request.user, recipient=other_user) |
                Q(sender=other_user, recipient=self.request.user)
            ).select_related('sender').order_by('-created').first()

            dialog_list.append(dialog)

        dialog_list.sort(
            key=lambda d: d.last_message.created if d.last_message else timezone.make_aware(datetime.min),
            reverse=True
        )
        return dialog_list


class DialogMessagesView(DialogsWithUnreadMixin, LoginRequiredMixin, ListView):
    template_name = 'profiles/messages.html'
    context_object_name = 'messages'
    paginate_by = 30

    def get_queryset(self):
        self.other_user = get_object_or_404(get_user_model(), pk=self.kwargs['user_id'])

        # НЕ помечаем сообщения как прочитанные здесь!
        # Это будет делать WebSocket при получении события msg_type: 11
        # Это предотвращает race condition

        return MessageModel.objects.filter(
            Q(sender=self.request.user, recipient=self.other_user) |
            Q(sender=self.other_user, recipient=self.request.user)
        ).select_related('sender', 'recipient').prefetch_related('reactions').order_by('-created')[:self.paginate_by]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['other_user'] = self.other_user

        # Получаем диалоги для сайдбара
        dialogs = self._get_dialogs_with_unread()
        
        context['dialogs'] = dialogs
        context['active_user_id'] = self.other_user.pk
        
        # Для правильного отображения в шаблоне (от старых к новым)
        context['messages'] = list(reversed(context['messages']))
        context['reaction_emojis'] = ALLOWED_REACTION_EMOJIS

        return context


class LoadMoreMessagesView(LoginRequiredMixin, View):
    """API endpoint для ленивой подгрузки сообщений"""
    
    def get(self, request, user_id):
        other_user = get_object_or_404(get_user_model(), pk=user_id)
        offset = int(request.GET.get('offset', 0))
        limit = 30
        
        messages = MessageModel.objects.filter(
            Q(sender=request.user, recipient=other_user) |
            Q(sender=other_user, recipient=request.user)
        ).select_related('sender', 'recipient').order_by('-created')[offset:offset + limit]
        
        messages_data = []
        for msg in reversed(list(messages)):
            messages_data.append({
                'id': msg.id,
                'text': msg.text,
                'sender_id': msg.sender.id,
                'sender_name': f"{msg.sender.last_name} {msg.sender.first_name}",
                'sender_avatar': msg.sender.avatar.url if msg.sender.avatar else None,
                'created': msg.created.strftime('%H:%M'),
                'file': msg.file.url if hasattr(msg, 'file') and msg.file else None,
            })
        
        total_messages = MessageModel.objects.filter(
            Q(sender=request.user, recipient=other_user) |
            Q(sender=other_user, recipient=request.user)
        ).count()
        
        return JsonResponse({
            'messages': messages_data,
            'has_more': total_messages > offset + limit
        })


class GetUnreadCountView(LoginRequiredMixin, View):
    """API endpoint для получения количества непрочитанных сообщений"""
    
    def get(self, request):
        # Проверяем кеш
        cache_key_total = f'unread_count_{request.user.pk}'
        cache_key_dialogs = f'dialogs_unread_{request.user.pk}'
        
        total_unread = cache.get(cache_key_total)
        dialogs_unread = cache.get(cache_key_dialogs)
        
        # Если хотя бы одно значение не в кеше, пересчитываем
        if total_unread is None or dialogs_unread is None:
            # Общее количество непрочитанных
            total_unread = MessageModel.objects.filter(
                recipient=request.user,
                read=False
            ).count()
            
            # Получаем все диалоги пользователя
            dialogs = DialogsModel.objects.filter(
                Q(user1=request.user) | Q(user2=request.user)
            ).select_related('user1', 'user2')
            
            # Получаем всех собеседников
            other_user_ids = []
            for dialog in dialogs:
                other_user = dialog.user1 if dialog.user2 == request.user else dialog.user2
                other_user_ids.append(other_user.pk)
            
            # Один запрос для подсчета непрочитанных от всех собеседников
            dialogs_unread = {}
            if other_user_ids:
                unread_counts = MessageModel.objects.filter(
                    sender_id__in=other_user_ids,
                    recipient=request.user,
                    read=False
                ).values('sender_id').annotate(count=Count('id'))
                
                for item in unread_counts:
                    dialogs_unread[str(item['sender_id'])] = item['count']
            
            # Кешируем на 30 секунд
            cache.set(cache_key_total, total_unread, 30)
            cache.set(cache_key_dialogs, dialogs_unread, 30)
        
        response_data = {
            'total_unread': total_unread,
            'dialogs_unread': dialogs_unread
        }
        
        return JsonResponse(response_data)


class DialogsListView(DialogsWithUnreadMixin, LoginRequiredMixin, ListView):
    template_name = 'profiles/dialogs.html'
    context_object_name = 'dialogs'

    def get_queryset(self):
        return self._get_dialogs_with_unread()


@method_decorator(login_required, name='dispatch')
class SendMessageView(View):
    def post(self, request, user_id):
        recipient = get_object_or_404(get_user_model(), pk=user_id)
        text = request.POST.get('text', '').strip()
        
        if text:
            MessageModel.objects.create(
                sender=request.user,
                recipient=recipient,
                text=text
            )
            # Создаем/обновляем диалог
            DialogsModel.create_if_not_exists(request.user, recipient)
            
            # Очищаем кеш ПОЛУЧАТЕЛЯ (не отправителя!)
            clear_user_cache(recipient.pk)
            
        return redirect('dialog_messages', user_id=user_id)


class MarkMessagesReadView(LoginRequiredMixin, View):
    """API endpoint для пометки сообщений как прочитанных"""
    
    def post(self, request, user_id):
        other_user = get_object_or_404(get_user_model(), pk=user_id)
        
        # Помечаем непрочитанные сообщения от собеседника как прочитанные
        updated_count = MessageModel.objects.filter(
            sender=other_user,
            recipient=request.user,
            read=False
        ).update(read=True)
        
        # Очищаем кеш текущего пользователя
        if updated_count > 0:
            clear_user_cache(request.user.pk)

            # Уведомляем отправителя, что все его сообщения в этом диалоге прочитаны
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(str(other_user.pk), {
                'type': 'message_read',
                'message_id': 0,
                'sender': str(request.user.pk),
                'receiver': str(other_user.pk),
            })

        return JsonResponse({
            'success': True,
            'marked_read': updated_count
        })


class EditMessageView(LoginRequiredMixin, View):
    """API endpoint для редактирования собственного сообщения"""

    def post(self, request, message_id):
        message = get_object_or_404(MessageModel, pk=message_id, sender=request.user)
        text = request.POST.get('text', '').strip()

        if not text:
            return JsonResponse({'success': False, 'error': 'Текст не может быть пустым'}, status=400)

        message.text = text
        message.save(update_fields=['text'])
        message.refresh_from_db(fields=['text'])

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(str(message.recipient_id), {
            'type': 'message_edited',
            'message_id': message.id,
            'text': message.text,
            'sender': str(request.user.pk),
            'receiver': str(message.recipient_id),
        })

        return JsonResponse({'success': True, 'text': message.text})


class DeleteMessageView(LoginRequiredMixin, View):
    """API endpoint для удаления собственного сообщения"""

    def post(self, request, message_id):
        message = get_object_or_404(MessageModel, pk=message_id, sender=request.user)
        recipient_id = message.recipient_id
        message.delete()

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(str(recipient_id), {
            'type': 'message_deleted',
            'message_id': message_id,
            'sender': str(request.user.pk),
            'receiver': str(recipient_id),
        })

        return JsonResponse({'success': True})


ALLOWED_REACTION_EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '😡']


class ToggleMessageReactionView(LoginRequiredMixin, View):
    """API endpoint для установки/снятия реакции на сообщение (своё или собеседника)"""

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
        elif existing:
            existing.emoji = emoji
            existing.save(update_fields=['emoji'])
        else:
            MessageReaction.objects.create(message=message, user=request.user, emoji=emoji)

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

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(str(other_user_id), {
            'type': 'message_reaction',
            'message_id': message.id,
            'reactions': reactions,
            'sender': str(request.user.pk),
            'receiver': str(other_user_id),
        })


# --- Личные задачи ---

class TaskListFeedView(LoginRequiredMixin, View):
    """Возвращает актуальный список собственных задач пользователя (без
    поручений от других) — используется для точечного обновления виджета
    «Мои задачи», когда задача была изменена из другого места (например,
    из виджета календаря)."""

    def _serialize(self, task):
        return {
            'id': task.id,
            'title': task.title,
            'description': task.description,
            'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
            'due_date_iso': task.due_date.isoformat() if task.due_date else '',
            'is_completed': task.is_completed,
            'status': task.status,
            'status_label': task.get_status_display(),
        }

    def get(self, request):
        own_tasks = Task.objects.filter(user=request.user).filter(Q(assigned_by__isnull=True) | Q(assigned_by=request.user))
        return JsonResponse({
            'pending': [self._serialize(t) for t in own_tasks.filter(is_completed=False)],
            'completed': [self._serialize(t) for t in own_tasks.filter(is_completed=True)],
        })


class TaskCreateView(LoginRequiredMixin, View):
    def post(self, request):
        form = TaskForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        task = form.save(commit=False)
        task.user = request.user
        task.save()
        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
                'due_date_iso': task.due_date.isoformat() if task.due_date else '',
                'is_completed': task.is_completed,
                'status': task.status,
                'status_label': task.get_status_display(),
            },
        })


def _task_participant_or_404(task_id, user):
    """Задача видна и исполнителю, и постановщику (если это разные люди)."""
    return get_object_or_404(
        Task.objects.filter(Q(user=user) | Q(assigned_by=user)),
        pk=task_id,
    )


class TaskStatusUpdateView(LoginRequiredMixin, View):
    """Меняет статус задачи. Доступно и исполнителю, и постановщику — оба видят
    один и тот же статус (синхронизирован через единое поле в модели)."""

    def post(self, request, task_id):
        task = _task_participant_or_404(task_id, request.user)
        status = request.POST.get('status')
        if status not in dict(Task.Status.choices):
            return JsonResponse({'success': False, 'error': 'Некорректный статус.'}, status=400)

        task.status = status
        task.save(update_fields=['status', 'is_completed'])

        # Постановщика уведомляем о смене статуса только если статус меняет
        # именно исполнитель — иначе постановщик уведомлял бы сам себя.
        if task.assigned_by_id and task.assigned_by_id != request.user.id:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                task.assigned_by, Notification.Kind.TASK_STATUS_CHANGED,
                f'{actor_name} изменил(а) статус задачи «{task.title}» на «{task.get_status_display()}»',
                actor=request.user, task=task,
            )

        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
                'due_date_iso': task.due_date.isoformat() if task.due_date else '',
                'is_completed': task.is_completed,
                'status': task.status,
                'status_label': task.get_status_display(),
                'toggle_url': reverse('task_toggle', kwargs={'task_id': task.id}),
                'delete_url': reverse('task_delete', kwargs={'task_id': task.id}),
                'edit_url': reverse('task_edit', kwargs={'task_id': task.id}),
            },
        })


class TaskToggleView(LoginRequiredMixin, View):
    """Переключение выполнено/не выполнено (используется чекбоксом в виджете
    «Мои задачи» для собственных задач без постановщика). Доступно и исполнителю,
    и постановщику."""

    def post(self, request, task_id):
        task = _task_participant_or_404(task_id, request.user)
        task.status = Task.Status.NOT_STARTED if task.is_completed else Task.Status.DONE
        task.save(update_fields=['status', 'is_completed'])
        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
                'due_date_iso': task.due_date.isoformat() if task.due_date else '',
                'is_completed': task.is_completed,
                'status': task.status,
                'status_label': task.get_status_display(),
                'toggle_url': reverse('task_toggle', kwargs={'task_id': task.id}),
                'delete_url': reverse('task_delete', kwargs={'task_id': task.id}),
                'edit_url': reverse('task_edit', kwargs={'task_id': task.id}),
            },
        })


class TaskDeleteView(LoginRequiredMixin, View):
    """Удалить задачу может только тот, кто её создал: постановщик (если задача
    кому-то поручена) либо сам исполнитель (если задача личная, без постановщика)."""

    def post(self, request, task_id):
        task = get_object_or_404(Task, pk=task_id)
        creator = task.assigned_by or task.user
        if creator != request.user:
            raise Http404
        task.delete()
        return JsonResponse({'success': True})


class TaskEditView(LoginRequiredMixin, View):
    """Редактировать задачу может только её постановщик (или сам исполнитель для
    личных задач без постановщика). При изменении помечает задачу как is_edited,
    чтобы исполнитель видел, что условия поменялись."""

    def post(self, request, task_id):
        task = get_object_or_404(Task, pk=task_id)
        creator = task.assigned_by or task.user
        if creator != request.user:
            raise Http404

        form = TaskEditForm(request.POST, instance=task)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        task = form.save(commit=False)
        is_assignment = task.assigned_by_id and task.assigned_by_id != task.user_id
        if is_assignment:
            task.is_edited = True
        task.save()

        if is_assignment:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                task.user, Notification.Kind.TASK_EDITED,
                f'{actor_name} изменил(а) задачу «{task.title}»',
                actor=request.user, task=task,
            )

        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'due_date': task.due_date.strftime('%d.%m') if task.due_date else None,
                'due_date_iso': task.due_date.isoformat() if task.due_date else '',
                'is_completed': task.is_completed,
                'status': task.status,
                'status_label': task.get_status_display(),
                'is_edited': task.is_edited,
            },
        })


# --- Личные заметки (стикеры) ---

def _serialize_note(note):
    return {
        'id': note.id,
        'content': note.content,
        'color': note.color,
    }


class NoteCreateView(LoginRequiredMixin, View):
    def post(self, request):
        form = NoteForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        note = form.save(commit=False)
        note.user = request.user
        max_position = Note.objects.filter(user=request.user).aggregate(m=Max('position'))['m']
        note.position = (max_position or 0) + 1
        note.save()
        return JsonResponse({'success': True, 'note': _serialize_note(note)})


class NoteEditView(LoginRequiredMixin, View):
    def post(self, request, note_id):
        note = get_object_or_404(Note, pk=note_id, user=request.user)
        form = NoteForm(request.POST, instance=note)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        note = form.save()
        return JsonResponse({'success': True, 'note': _serialize_note(note)})


class NoteDeleteView(LoginRequiredMixin, View):
    def post(self, request, note_id):
        get_object_or_404(Note, pk=note_id, user=request.user).delete()
        return JsonResponse({'success': True})


class TaskQuickCreateView(LoginRequiredMixin, View):
    """Быстрое создание задачи из заметки (AJAX): название берётся из
    выделенного в заметке текста, либо из первых 100 символов заметки."""

    def post(self, request):
        title = request.POST.get('title', '').strip()[:255]
        if not title:
            return JsonResponse({'success': False, 'error': 'Пустое название задачи.'}, status=400)

        task = Task.objects.create(user=request.user, title=title)
        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'due_date': None,
                'toggle_url': reverse('task_toggle', kwargs={'task_id': task.id}),
                'delete_url': reverse('task_delete', kwargs={'task_id': task.id}),
            },
        })


# --- Календарь ---

class EventCreateView(LoginRequiredMixin, View):
    """Создаёт личное напоминание (AJAX, JSON) — только для себя, без исполнения."""

    def post(self, request):
        form = EventForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        event = form.save(commit=False)
        event.user = request.user
        event.created_by = request.user
        event.event_type = Event.EventType.PERSONAL
        event.save()

        return JsonResponse({'success': True, 'event': {'id': event.id}})


class EventDeleteView(LoginRequiredMixin, View):
    def post(self, request, event_id):
        get_object_or_404(Event, pk=event_id, user=request.user, event_type=Event.EventType.PERSONAL).delete()
        return JsonResponse({'success': True})


class CalendarUsersListView(LoginRequiredMixin, View):
    """Список пользователей для выбора исполнителя задачи через календарь (AJAX)."""

    def get(self, request):
        query = request.GET.get('q', '').strip()
        qs = get_user_model().objects.exclude(pk=request.user.pk)
        if query:
            qs = qs.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(username__icontains=query))
        users = [
            {'id': u.id, 'name': f'{u.first_name} {u.last_name}'.strip() or u.username}
            for u in qs.order_by('first_name', 'last_name')[:20]
        ]
        return JsonResponse({'users': users})


class NotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=['is_read'])
        return JsonResponse({'success': True})


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return JsonResponse({'success': True})


class CalendarTaskCreateView(LoginRequiredMixin, View):
    """Постановка задачи через календарь: себе (без исполнителя) или другому
    сотруднику (с указанием исполнителя и постановщика)."""

    def post(self, request):
        assignee_id = request.POST.get('assignee_id', '').strip()
        assignee = get_object_or_404(get_user_model(), pk=assignee_id) if assignee_id else request.user

        form = TaskEditForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        if not form.cleaned_data.get('due_date'):
            return JsonResponse({'success': False, 'errors': {'due_date': ['Укажите дату.']}}, status=400)

        task = form.save(commit=False)
        task.user = assignee
        task.assigned_by = request.user
        task.save()

        if assignee.id != request.user.id:
            actor_name = f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username
            notify(
                assignee, Notification.Kind.TASK_ASSIGNED,
                f'{actor_name} поставил(а) вам задачу «{task.title}»',
                actor=request.user, task=task,
            )

        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'due_date_iso': task.due_date.isoformat(),
                'status': task.status,
                'status_label': task.get_status_display(),
                'assignee_name': f'{assignee.first_name} {assignee.last_name}'.strip() or assignee.username,
                'is_own': assignee_id == '' or int(assignee_id) == request.user.id,
            },
        })


def _expand_recurrence(event, window_start, window_end):
    """Виртуально повторяет событие внутри окна [window_start, window_end],
    не создавая новых записей в БД. Возвращает список дат."""
    if event.recurrence == Event.Recurrence.NONE:
        return [event.date] if window_start <= event.date <= window_end else []

    dates = []
    current = event.date
    step_years = 1 if event.recurrence == Event.Recurrence.YEARLY else 0
    step_months = 1 if event.recurrence == Event.Recurrence.MONTHLY else 0
    step_weeks = 1 if event.recurrence == Event.Recurrence.WEEKLY else 0

    # Отматываем назад до начала окна, затем идём вперёд, чтобы не пропустить повторы
    while current > window_start:
        current = _shift_date(current, -step_years, -step_months, -7 * step_weeks)

    while current <= window_end:
        if current >= window_start and current >= event.date:
            dates.append(current)
        current = _shift_date(current, step_years, step_months, 7 * step_weeks)

    return dates


def _shift_date(d, years, months, days):
    if years:
        try:
            return d.replace(year=d.year + years)
        except ValueError:
            # 29 февраля в невисокосный год — сдвигаем на 28-е
            return d.replace(year=d.year + years, day=28)
    if months:
        month = d.month - 1 + months
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return d.replace(year=year, month=month, day=day)
    if days:
        return d + timedelta(days=days)
    return d


# Единая палитра маркеров календаря: цвет обозначает тип пункта, а не его
# статус (выполнено/нет) — при выполнении задачи маркер не меняет цвет.
CALENDAR_COLORS = {
    'my_task': '#0077FF',
    'birthday': '#F78B00',
    'assigned_task': '#8B5CF6',
    'reminder': '#14B8A6',
}


class CalendarEventsFeedView(LoginRequiredMixin, View):
    """JSON-фид событий для календаря: личные события пользователя + корпоративные + дни рождения."""

    def get(self, request):
        user = request.user
        events = Event.objects.filter(
            Q(user=user, event_type=Event.EventType.PERSONAL) | Q(event_type=Event.EventType.CORPORATE)
        )

        window_start = date.today() - timedelta(days=365)
        window_end = date.today() + timedelta(days=365)

        items = []
        for e in events:
            for occurrence_date in _expand_recurrence(e, window_start, window_end):
                items.append({
                    'id': e.id,
                    'is_task': False,
                    'title': e.title,
                    'description': e.description,
                    'start': occurrence_date.isoformat(),
                    'allDay': True,
                    'color': CALENDAR_COLORS['reminder'],
                    'extendedProps': {
                        'type': e.event_type,
                        'deletable': e.created_by_id == user.id,
                        'isCompleted': False,
                        'isRecurring': e.recurrence != Event.Recurrence.NONE,
                    },
                })

        # Задачи с дедлайном: собственные задачи исполнителя + задачи, которые
        # пользователь поставил другим (постановщик тоже видит их в своём календаре).
        tasks = Task.objects.filter(
            Q(user=user) | Q(assigned_by=user)
        ).filter(due_date__isnull=False).select_related('user', 'assigned_by')

        for t in tasks:
            is_assignment = bool(t.assigned_by_id and t.assigned_by_id != t.user_id)
            is_creator_view = is_assignment and t.assigned_by_id == user.id

            if is_assignment and not is_creator_view:
                color = CALENDAR_COLORS['assigned_task']
            else:
                color = CALENDAR_COLORS['my_task']

            assigner_name = ''
            assignee_name = ''
            if is_assignment:
                assigner_name = f'{t.assigned_by.first_name} {t.assigned_by.last_name}'.strip() or t.assigned_by.username
                assignee_name = f'{t.user.first_name} {t.user.last_name}'.strip() or t.user.username

            items.append({
                'id': t.id,
                'is_task': True,
                'title': t.title,
                'description': t.description,
                'start': t.due_date.isoformat(),
                'allDay': True,
                'color': color,
                'extendedProps': {
                    'type': 'task',
                    'deletable': is_creator_view,
                    'isCompleted': t.is_completed,
                    'isAssignment': is_assignment,
                    'status': t.status,
                    'statusLabel': t.get_status_display(),
                    'isEdited': t.is_edited,
                    'assignerName': assigner_name,
                    'assigneeName': assignee_name if is_creator_view else '',
                    'toggleUrl': reverse('task_toggle', kwargs={'task_id': t.id}),
                    'statusUrl': reverse('task_status_update', kwargs={'task_id': t.id}),
                    'deleteUrl': reverse('task_delete', kwargs={'task_id': t.id}),
                    'editUrl': reverse('task_edit', kwargs={'task_id': t.id}),
                    'canEdit': is_creator_view,
                },
            })

        # Дни рождения — виртуальные события без Event-записи, повторяются каждый год
        for u in get_user_model().objects.exclude(birthday__isnull=True):
            items.append({
                'is_task': False,
                'title': f'🎂 День рождения у {u.first_name} {u.last_name}',
                'description': '',
                'start': f'{date.today().year}-{u.birthday.month:02d}-{u.birthday.day:02d}',
                'allDay': True,
                'color': CALENDAR_COLORS['birthday'],
                'extendedProps': {'type': 'birthday', 'deletable': False, 'isCompleted': False},
            })

        return JsonResponse(items, safe=False)


# --- Статус сотрудника ---

class EmployeeStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request):
        User = get_user_model()
        status = request.POST.get('status')
        if status in dict(User.EmployeeStatus.choices):
            request.user.employee_status = status
            request.user.save(update_fields=['employee_status'])
        return JsonResponse({'success': True, 'status': status, 'label': request.user.get_employee_status_display()})

        return JsonResponse({'success': True, 'reactions': reactions})