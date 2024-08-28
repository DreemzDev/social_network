from django.urls import path

from .views import*
from . import views

urlpatterns = [
    # path('gallery/', views.gallery_with_upload, name='gallery'),
    path('gallery/', AddImages.as_view(), name='gallery'),
]