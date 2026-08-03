"""
URL configuration for social_network project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from __future__ import unicode_literals, absolute_import

from django.urls import path, re_path, include
from django.contrib import admin
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import get_user_model
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse, Http404
from django.contrib.auth.models import AbstractBaseUser

from typing import List


UserModel = get_user_model()


class UsersListView(LoginRequiredMixin, ListView):
    http_method_names = ['get', ]

    def get_queryset(self):
        return UserModel.objects.all().exclude(id=self.request.user.id)

    def render_to_response(self, context, **response_kwargs):
        users: List[AbstractBaseUser] = context['object_list']

        data = [{
            "username": user.get_username(),
            "pk": str(user.pk)
        } for user in users]
        return JsonResponse(data, safe=False, **response_kwargs)
urlpatterns = [
    path('admin/', admin.site.urls),

    path('', include('posts.urls')),
    path('', include('category.urls')),
    path('', include('register.urls')),
    path('', include('login.urls')),
    path('', include('profiles.urls')),
    path('', include('comments.urls')),
    path('', include('gallery.urls')),
    path('', include('phonebook.urls')),
    re_path(r'', include('django_private_chat2.urls', namespace='django_private_chat2'))
]
# Файлы storage лежат внутри MEDIA_ROOT, но раздавать их как обычную
# медиатеку нельзя: путь предсказуем (/media/storage/blobs/<checksum>), и
# прямая ссылка обходила бы ВСЮ проверку прав — приватный документ отдела
# читался бы даже анонимом. Скачивание возможно только через view
# модуля-потребителя, который сначала проверяет права, а затем зовёт
# StorageService.get_download_response() (ARCHITECTURE.md, раздел 8).
# В проде тот же путь дополнительно закрывается на уровне nginx
# (location /media/storage/ { deny all; }) — здесь защита на случай
# runserver/DEBUG и как страховка от неверного конфига веб-сервера.
def _storage_media_forbidden(request, *args, **kwargs):
    raise Http404('Прямой доступ к файлам storage запрещён')


urlpatterns += [
    re_path(r'^media/storage/', _storage_media_forbidden),
]
urlpatterns +=static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()