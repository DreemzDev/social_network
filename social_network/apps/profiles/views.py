from typing import Any
from django.urls import reverse_lazy
from posts.models import Post
from posts.forms import AddPostForm
from posts.views import AddPost
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone
from django.http import Http404
from profiles.forms import AddProfileForm, SettingProfileForm
from django.contrib.auth.models import User
# from profiles.models import Profile
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
from django.contrib.auth import get_user_model
from django.utils.deprecation import MiddlewareMixin
from django.views.generic.edit import FormMixin
from category.models import Category
from phonebook.models import Phonebook
from datetime import date
from django.db.models import Q
from phonebook.forms import UpdateBookForm



# class ShowProfile(ListView):
    
    # model = get_user_model()
    # template_name = 'profiles/profiles.html'

    # def get_context_data(self, *, object_list=None, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     context["user_list"] = get_user_model().objects.all()
    #     return context
   
   
    

# class UserProfileView(TemplateView):
#     template_name = 'profiles/profiles.html'
#     model = get_user_model()


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
    success_url = reverse_lazy('home')
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        
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
        context["sh_online"] = get_user_model().objects.filter(online=True)
        context["cats"] = Category.objects.all()
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
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
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context

