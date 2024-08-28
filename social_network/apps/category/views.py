from django.views.generic import ListView
from django.shortcuts import render
from django.db.models import Count
from phonebook.models import Phonebook
from category.models import Category
from posts.models import Post
from django.contrib.auth import get_user_model
# Create your views here.
class PostCategory(ListView):
    paginate_by = 3
    model = Post
    template_name = 'index.html'
    context_object_name = 'posts'
    def get_queryset(self):
        return Post.objects.filter(author__cat__id=self.kwargs['cat_id'])# 
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        
        return context

class UserCategory(ListView):  
    model = get_user_model()
    template_name = 'profiles/all_users.html'
    context_object_name = 'sh_users'
    def get_queryset(self):
        return get_user_model().objects.filter(cat__id=self.kwargs['cat_id'])# 
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        
        return context

class PhoneCategory(ListView):  
    model = get_user_model()
    template_name = 'profiles/phones.html'
    context_object_name = 'phones'
    def get_queryset(self):
        return get_user_model().objects.filter(cat__id=self.kwargs['cat_id'])# 
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        context['books'] = Phonebook.objects.all()
        
        return context