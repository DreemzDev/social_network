from typing import Any

from urllib import request
from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView, UpdateView
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from category.models import Category
from comments.forms import CommentForm
from comments.models import Comment
from .forms import *
from .models import Post
from django.core.paginator import Paginator
from django.views.generic.detail import SingleObjectMixin



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
    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context['title'] = Category.objects.all()
    #     return context



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
    fields = ['title', 'content','photo','cat']
    template_name = 'post/setting_post.html'
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('home') 
 