from django.db.models import Count, F, Q
from typing import Any
from django.contrib import messages
from datetime import date, timedelta
from urllib import request
from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView, UpdateView, DeleteView
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from category.models import Category
from comments.forms import CommentForm
from comments.models import Comment
from .forms import *
from .models import Post, PostImage, PostFile
from profiles.models import Task, Note
from profiles.forms import TaskForm, NoteForm, EventForm
from django.core.paginator import Paginator
from django.views.generic.detail import SingleObjectMixin
# from dateutil.relativedelta import relativedelta
# from django.core import serializers
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone


class PortalHome(LoginRequiredMixin, ListView):
    paginate_by = 10
    model = Post
    template_name = 'index.html'
    context_object_name = 'posts'
    login_url = 'login'
    

    def get_queryset(self):
        return Post.objects.annotate(num_comments=Count('post_comments')).prefetch_related('images', 'files').order_by('-time_create')
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        # context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        # dt =date.today().day+1
        # context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context
    



class ShowPost(FormMixin, DetailView):
    
    model = Post
    template_name = 'post/single.html'
    context_object_name = 'post'
    pk_url_kwarg = 'post_id'
    form_class = CommentForm

    def get_queryset(self):
        return Post.objects.prefetch_related('images', 'files')
    
  
    def get_success_url(self, **kwargs):
        return reverse_lazy('post', kwargs={'post_id': self.get_object().id})
    
    def post(self, request, *args, **kwargs):
        form = self.get_form()
        return self.form_valid(form)

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.post = self.get_object()
        self.object.comment_author = self.request.user
        self.object.save()
        return super().form_valid(form)
    
    def get(self, request, *args, **kwargs):
        post = self.get_object()
        if request.user.is_authenticated:
            post.viewers.add(request.user)
        return super().get(request, *args, **kwargs)

def toggle_like(request, post_id):
    if request.method == 'POST':
        post = get_object_or_404(Post, id=post_id)
        if request.user in post.likes.all():
            post.likes.remove(request.user)
            liked = False
        else:
            post.likes.add(request.user)
            liked = True
        return JsonResponse({'liked': liked, 'likes_count': post.likes.count()})   

class AddPost(FormView, TemplateView):
    
    form_class = AddPostForm
    template_name = 'profiles/profiles.html'
    success_url = reverse_lazy('home')
    
    def form_valid(self, form):
        post = form.save(commit=False)
        post.author = self.request.user
        post.save()

        for order, image_file in enumerate(self.request.FILES.getlist('images')):
            PostImage.objects.create(post=post, image=image_file, order=order)

        for uploaded_file in self.request.FILES.getlist('attachments'):
            PostFile.objects.create(post=post, file=uploaded_file, original_name=uploaded_file.name)

        return super().form_valid(form)

    def get_online_users(self):
        """Возвращает пользователей, которые были активны в последние 5 минут"""
        User = get_user_model()
        time_threshold = timezone.now() - timedelta(minutes=5)
        return User.objects.filter(last_activity__gte=time_threshold)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()
        
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        user_list = User.objects.all().order_by('-last_activity')
        
        # Считаем онлайн пользователей
        online_count = sum(1 for u in user_list if u.is_online)

        context['user_list'] = user_list  # Используем уже созданный user_list
        context["sh_online"] = self.get_online_users()
        context['posts'] = Post.objects.annotate(num_comments=Count('post_comments')).prefetch_related('images', 'files').filter(author=user).order_by('-time_create')
        context['user'] = user
        context['profile_user'] = user
        context['online_count'] = online_count  # Добавляем в контекст!

        user_posts = Post.objects.filter(author=user)
        context['stats'] = {
            'posts_count': user_posts.count(),
            'comments_count': Comment.objects.filter(comment_author=user).count(),
            'likes_count': user_posts.aggregate(total=Count('likes'))['total'] or 0,
            'views_count': user_posts.aggregate(total=Count('viewers'))['total'] or 0,
        }
        context['is_owner'] = self.request.user == user

        if context['is_owner']:
            own_tasks = Task.objects.filter(user=user).filter(Q(assigned_by__isnull=True) | Q(assigned_by=user))
            context['pending_tasks'] = own_tasks.filter(is_completed=False)
            context['completed_tasks'] = own_tasks.filter(is_completed=True)
            context['task_form'] = TaskForm()

            context['notes'] = Note.objects.filter(user=user)
            context['note_form'] = NoteForm()

            context['event_form'] = EventForm()

        return context
  
class PostDeleteView(DeleteView):
    model = Post
    success_url = reverse_lazy('home')  # или куда нужно

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return HttpResponse(status=204)
        messages.success(request, 'Пост удалён')
        return super().delete(request, *args, **kwargs)

    

class HelpView(TemplateView):
    template_name = 'post/help.html'


class SettingPost( LoginRequiredMixin, UpdateView):
    form_class = AddPostForm
    model = Post

    template_name = 'post/setting_post.html'
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('home')
    context_object_name = 'post'

    def form_valid(self, form):
        response = super().form_valid(form)
        post = self.object

        remove_image_ids = self.request.POST.getlist('remove_image_ids')
        if remove_image_ids:
            PostImage.objects.filter(post=post, id__in=remove_image_ids).delete()

        remove_file_ids = self.request.POST.getlist('remove_file_ids')
        if remove_file_ids:
            PostFile.objects.filter(post=post, id__in=remove_file_ids).delete()

        existing_count = post.images.count()
        for order, image_file in enumerate(self.request.FILES.getlist('images')):
            PostImage.objects.create(post=post, image=image_file, order=existing_count + order)

        for uploaded_file in self.request.FILES.getlist('attachments'):
            PostFile.objects.create(post=post, file=uploaded_file, original_name=uploaded_file.name)

        return response
    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context['cats'] = Category.objects.all()
    #     return context