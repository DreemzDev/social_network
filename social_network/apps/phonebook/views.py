from django.http import Http404
from django.shortcuts import render, get_object_or_404
from django.views.generic import DetailView, ListView, CreateView, UpdateView, TemplateView, View, FormView
from phonebook.models import Phonebook
from phonebook.forms import UpdateBookForm
from django.views.generic.edit import FormMixin
from django.urls import reverse_lazy
from datetime import date
from django.contrib.auth import get_user_model

from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.models import FileObject
from storage.services import StorageService

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

    def form_valid(self, form):
        # Файл — не поле ModelForm (см. UpdateBookForm), обрабатывается
        # отдельно через StorageService, только если реально загружен новый.
        uploaded = self.request.FILES.get('book')
        if uploaded:
            try:
                file_object = StorageService.upload(
                    uploaded, user=self.request.user, category=FileObject.Category.CATALOG,
                )
            except (FileTooLargeError, QuotaExceededError) as e:
                form.add_error(None, str(e))
                return self.form_invalid(form)

            old_file_object = form.instance.file_object
            form.instance.file_object = file_object
            response = super().form_valid(form)

            if old_file_object:
                StorageService.detach(old_file_object, user=self.request.user, consumer='phonebook.Phonebook')
            return response

        return super().form_valid(form)

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        # context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        # dt =date.today().day+1
        # context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context


class PhonebookViewFileView(View):
    """Отдаёт файл справочника для просмотра в <iframe> (inline, не
    attachment) — та же логика прав, что и у PhoneBook (открыто всем,
    LoginRequiredMixin здесь исторически не стоял, поведение сохранено)."""

    def get(self, request, book_id):
        book = get_object_or_404(Phonebook, pk=book_id)
        if not book.file_object:
            raise Http404
        return StorageService.get_download_response(book.file_object, request, inline=True)