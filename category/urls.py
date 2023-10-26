from django.urls import path

from .views import*

urlpatterns = [
    path('category/<int:cat_id>/', PostCategory.as_view(), name='category'), #
      
]