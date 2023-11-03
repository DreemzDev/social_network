from typing import Any
from urllib import request
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from category.models import Category
from .forms import *
from .models import Post
from django.core.paginator import Paginator



class PortalHome(LoginRequiredMixin, ListView):
    paginate_by = 3
    model = Post
    template_name = 'post/index.html'
    context_object_name = 'posts'
    login_url = 'login'
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
       
        return context
  



class ShowPost( DetailView):
    model = Post
    template_name = 'post/single.html'
    context_object_name = 'post'
    pk_url_kwarg = 'post_id'
    
    # def form_valid(self, form):
    #     post = form.save(commit=False)
    #     post.author = self.request.user
        
    #     return super().form_valid(form)
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
       
        return context

class AddPost(FormView):
    
    form_class = AddPostForm
    template_name = 'profiles/profiles.html'
    # pk_url_kwarg = 'user_username'
    
    def get_context_data(self, *, object_list=None, **kwargs):
        
        context = super().get_context_data(**kwargs)
        context['posts'] = Post.objects.filter(author = self.request.user)
        
        return context
    
    def form_valid(self, form):
        
        
        post = form.save(commit=False)
        post.author = self.request.user
        form.save()
        return super().form_valid(form)
   
    success_url = reverse_lazy('home')
    
class SettingPost( LoginRequiredMixin, UpdateView):
    model = Post
    # form_class = AddPostForm
    fields = ['title', 'content','photo','cat']
    template_name = 'post/setting_post.html'
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('home') 
    # permission_required = 'post.change_Post'
    
    
    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context['title'] = 'Добавление статьи'
    #     return context