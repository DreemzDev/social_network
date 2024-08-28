from django.urls import path

from .views import*
from . import views

urlpatterns = [
    

    path('phonebook/<int:book_id>/', PhoneBook.as_view(), name='phonebook'), 
  
   


    
]