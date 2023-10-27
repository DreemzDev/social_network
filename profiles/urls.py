from django.urls import path, re_path

from .views import*

urlpatterns = [
 
# path('profile/<str:username>/', UserProfile.as_view(), name='user_profile'), #раб

path('profile/<str:username>/', ShowProfile.as_view(), name='profile'),

path('addprofile/<int:user_id>/', AddProfile.as_view(), name='addprofile'), 
path('settingprofile/<int:user_id>/', SettingProfile.as_view(), name='settingprofile'),
path('users/', ShowUsers.as_view(), name='show_users'),


]