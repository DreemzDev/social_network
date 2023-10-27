from django.urls import path

from .views import*

urlpatterns = [
    path('', PortalHome.as_view(), name='home'), #Маршрут для функции index
   
    path('post/<int:post_id>/', ShowPost.as_view(), name='post'), 
   
    path('profile/<str:username>', AddPost.as_view(), name='addpost'), 
    
    
   
   
]