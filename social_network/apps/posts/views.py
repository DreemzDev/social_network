from django.db.models import Count
from typing import Any

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
from .models import Post
from django.core.paginator import Paginator
from django.views.generic.detail import SingleObjectMixin
# from dateutil.relativedelta import relativedelta
# from django.core import serializers
from django.http import HttpResponse
try:
    from django.utils import simplejson as json
except ImportError:
    import json
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST



class PortalHome(LoginRequiredMixin, ListView):
    paginate_by = 10
    model = Post
    template_name = 'index.html'
    context_object_name = 'posts'
    login_url = 'login'
    

    def get_queryset(self):
        return Post.objects.annotate(num_comments=Count('post_comments')).order_by('-time_create')
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context
    



class ShowPost(FormMixin, DetailView):
    
    model = Post
    template_name = 'post/single.html'
    context_object_name = 'post'
    pk_url_kwarg = 'post_id'
    form_class = CommentForm
    
  
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
    
    

class AddPost(FormView, TemplateView):
    
    form_class = AddPostForm
    template_name = 'profiles/profiles.html'
    success_url = reverse_lazy('home')
    
    def form_valid(self, form):
        post = form.save(commit=False)
        post.author = self.request.user
        form.save()
        return super().form_valid(form)
    

    def add_remove_like(request, pk):
        data = {}
        post = Post.objects.get(pk=pk)

        if request.method == "POST":
            user = request.user
            if post.likes.filter(id=user.id).exists():
                liked = False
                post.likes.remove(user)
            else:
                post.likes.add(user)
                liked = True


        data["count"] = Post.likes.count()
        data["liked"] = liked
        return JsonResponse(data) 

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user_list'] = get_user_model().objects.all()
        context["sh_online"] = get_user_model().objects.filter(online=True)
        user = get_object_or_404(get_user_model(), username=self.kwargs.get('username'))
        context['posts'] = Post.objects.annotate(num_comments=Count('post_comments')).filter(author=user).order_by('-time_create')
        context['user'] = user
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context
  
    


    

class SettingPost( LoginRequiredMixin, UpdateView):
    model = Post
    fields = ['content','photo']
    template_name = 'post/setting_post.html'
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('home') 
    context_object_name = 'post'
    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context['cats'] = Category.objects.all()
    #     return context