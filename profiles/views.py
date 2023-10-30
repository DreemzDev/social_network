from django.urls import reverse_lazy
from category.models import Category
from post.forms import AddPostForm
from post.views import AddPost
from django.core.cache import cache

from django.utils import timezone

from profiles.forms import AddProfileForm, SettingProfileForm
from django.contrib.auth.models import User
# from profiles.models import Profile
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
from django.contrib.auth import get_user_model
from django.utils.deprecation import MiddlewareMixin

# Create your views here.

def all_users(request):
    users = get_user_model().objects.all()
    return render(request, 'all_users.html', {'users': users})





class ShowProfile( CreateView, DetailView):
    

    model = get_user_model()
    template_name = 'profiles/profiles.html'
    context_object_name = 'profile'
    pk_url_kwarg = 'username'
 
   
    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        return render(request, 'profiles/profiles.html', {'user': user})

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        
        return context
    


class AddProfile(UpdateView):
    model = get_user_model()
    form_class = AddProfileForm
    template_name = 'profiles/addprofile.html'
    pk_url_kwarg = 'user_id'
    success_url = reverse_lazy('home')

class SettingProfile(UpdateView):
    model = get_user_model()
    form_class = SettingProfileForm
    template_name = 'profiles/settingprofiles.html'
    pk_url_kwarg = 'user_id'
    success_url = reverse_lazy('home')

class ShowUsers(ListView):

    model = get_user_model()
    template_name = 'profiles/all_users.html'
    context_object_name = 'sh_users'
class ActiveUserMiddleware(MiddlewareMixin):



    def process_request(self, request):

        if request.user.is_authenticated and request.session.session_key:

            cache_key = f'last-seen-{request.user.id}'

            last_login = cache.get(cache_key)



            if not last_login:

                User.objects.filter(id=request.user.id).update(last_login=timezone.now())

                # Устанавливаем кэширование на 300 секунд с текущей датой по ключу last-seen-id-пользователя

                cache.set(cache_key, timezone.now(), 300)