from django.shortcuts import render
from django.views.generic import DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
from phonebook.models import Phonebook
from phonebook.forms import UpdateBookForm
from django.views.generic.edit import FormMixin
from django.urls import reverse_lazy
from datetime import date
from django.contrib.auth import get_user_model
# Create your views here.

# class PhoneBook(CreateView):
#     model = Phonebook
#     form_class = UpdateBookForm
#     pk_url_kwarg = 'book_id'
    
class PhoneBook(UpdateView, DetailView):
    model = Phonebook
    form_class = UpdateBookForm
    template_name = 'phonebook/updatebook.html'
    pk_url_kwarg = 'book_id'
    context_object_name = 'books'
    success_url = reverse_lazy('home')
    def get_success_url(self, **kwargs):
        return reverse_lazy('phonebook', kwargs={'book_id': self.get_object().id})
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context