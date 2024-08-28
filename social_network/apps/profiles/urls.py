from django.urls import path

from .views import*

urlpatterns = [
 
# path('profile/<str:username>/', UserProfileView.as_view(), name='profile'),

path('addprofile/<int:user_id>/', AddProfile.as_view(), name='addprofile'), 

path('settingprofile/<int:user_id>/', SettingProfile.as_view(), name='settingprofile'),

path('users/', ShowUsers.as_view(), name='show_users'),

path('phones/', ShowPhones.as_view(), name='show_phones'),

]