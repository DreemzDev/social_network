from django.urls import path

from .views import*

urlpatterns = [
    path('category/<int:cat_id>/', PostCategory.as_view(), name='category'), #
    path('filterUsers/<int:cat_id>/', UserCategory.as_view(), name='filterUsers'), #
    path('filterPhones/<int:cat_id>/', PhoneCategory.as_view(), name='filterPhones'), #
      
]