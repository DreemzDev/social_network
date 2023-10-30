from typing import Any
from urllib import request
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView
from category.models import Category
from .forms import *
from .models import Post



class PortalHome(ListView):
    paginate_by = 3
    model = Post
    template_name = 'post/index.html'
    context_object_name = 'posts'
   
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
       
        return context
  



class ShowPost(DetailView):
    model = Post
    template_name = 'post/single.html'
    context_object_name = 'post'
    pk_url_kwarg = 'post_id'
    
    # def form_valid(self, form):
    #     post = form.save(commit=False)
    #     post.author = self.request.user
        
    #     return super().form_valid(form)


class AddPost(FormView):
    # model = Post
    form_class = AddPostForm
    template_name = 'profiles/profiles.html'
    # pk_url_kwarg = 'user_username'
    
    
    def form_valid(self, form):
        
        
        post = form.save(commit=False)
        post.author = self.request.user
        form.save()
        return super().form_valid(form)
   
    success_url = reverse_lazy('home')
    
    

    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context['title'] = 'Добавление статьи'
    #     return context