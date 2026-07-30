from django.urls import path

from .views import *

urlpatterns = [
    path('', PortalHome.as_view(), name='home'),

    path('post/<int:post_id>/', ShowPost.as_view(), name='post'),
    path('post/<int:post_id>/likers/', post_likers, name='post_likers'),

    path('profile/<str:username>/', AddPost.as_view(), name='addpost'),

    path('settingpost/<int:post_id>/', SettingPost.as_view(), name='settingpost'),

    path('toggle_like/<int:post_id>/', toggle_like, name='toggle_like'),

    path('deletepost/<int:pk>/', PostDeleteView.as_view(), name='delete-post'),

    path('help/', HelpView.as_view(), name='help'),
]