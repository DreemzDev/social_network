from django.views.generic import ListView
from django.shortcuts import render
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from phonebook.models import Phonebook
from category.models import Category
from posts.models import Post
from django.contrib.auth import get_user_model
# Create your views here.
class PostCategory(ListView):
    paginate_by = 10
    model = Post
    template_name = 'index.html'
    context_object_name = 'posts'
    def get_queryset(self):
        return Post.objects.filter(author__cat__id=self.kwargs['cat_id'])# 
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        context['current_cat_id'] = int(self.kwargs.get('cat_id', 0))  # текущий фильтр
        return context

class UserCategory(ListView):  
    model = get_user_model()
    template_name = 'profiles/all_users.html'
    context_object_name = 'sh_users'

    def get_queryset(self):
        return get_user_model().objects.filter(cat__id=self.kwargs['cat_id'])

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        cat_id = self.kwargs.get('cat_id')
        context['cats'] = Category.objects.all()
        context['current_cat_id'] = int(cat_id)
        User = get_user_model()
        time_threshold = timezone.now() - timedelta(minutes=5)
        context['sh_online'] = User.objects.filter(last_activity__gte=time_threshold, cat__id=cat_id)

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
        context['current_cat_id'] = int(self.kwargs.get('cat_id', 0))  # текущий фильтр
        return context