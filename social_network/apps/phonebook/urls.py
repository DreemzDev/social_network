from django.urls import path

from . import views

urlpatterns = [
    path('phonebook/add/', views.PhonebookCreateView.as_view(), name='phonebook_add'),
    path('phonebook/<int:book_id>/', views.PhoneBook.as_view(), name='phonebook'),
    path('phonebook/<int:book_id>/view/', views.PhonebookViewFileView.as_view(), name='phonebook_view_file'),
    path('phonebook/<int:book_id>/convert/', views.PhonebookConvertView.as_view(), name='phonebook_convert'),
    path('phonebook/<int:book_id>/conversion/', views.PhonebookConversionStatusView.as_view(), name='phonebook_conversion_status'),
    path('phonebook/<int:book_id>/delete/', views.PhonebookTrashView.as_view(), name='phonebook_delete'),
    path('phonebook/<int:book_id>/restore/', views.PhonebookRestoreView.as_view(), name='phonebook_restore'),
]
