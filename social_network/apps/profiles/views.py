from typing import Any
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import (
    DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
)
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin

from posts.models import Post
from posts.forms import AddPostForm
from posts.views import AddPost
from profiles.forms import AddProfileForm, SettingProfileForm
from category.models import Category
from phonebook.models import Phonebook
from phonebook.forms import UpdateBookForm
from django_private_chat2.models import MessageModel, DialogsModel
from .models import MessageReaction
from datetime import date
from django.http import JsonResponse
from django.utils import timezone
from datetime import datetime
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


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


        return context

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

        return JsonResponse({'success': True, 'reactions': reactions})