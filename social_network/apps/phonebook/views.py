"""Справочники организации — документы, которые сотрудник открывает читать.

Файл хранится через storage и показывается в <iframe>. Править может любой
сотрудник: ответственность здесь даёт подпись (created_by/updated_by), а не
запрет. Удаление мягкое — базовыми вьюхами storage.fmviews.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, View

from storage import limits
from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.fmviews import RestoreObjectView, TrashObjectView
from storage.models import FileObject
from storage.services import StorageService

from .forms import PhonebookCreateForm, PhonebookForm
from .models import Phonebook

CONSUMER = 'phonebook.Phonebook'


class PhonebookFormMixin:
    """Приём файла, общий для добавления и правки справочника."""

    model = Phonebook

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['max_upload_size'] = limits.max_upload_size()
        return context

    def form_valid(self, form):
        uploaded = form.cleaned_data.get('book')
        old_file_object = form.instance.file_object

        if uploaded:
            try:
                form.instance.file_object = StorageService.upload(
                    uploaded, user=self.request.user, category=FileObject.Category.CATALOG,
                )
            except (FileTooLargeError, QuotaExceededError) as e:
                form.add_error('book', str(e))
                return self.form_invalid(form)

        response = super().form_valid(form)

        # Отвязка только после успешного сохранения: detach уводит blob в
        # ORPHAN, и упади сохранение между делом — запись осталась бы
        # ссылаться на файл, помеченный к удалению.
        if uploaded and old_file_object and old_file_object != form.instance.file_object:
            StorageService.detach(old_file_object, user=self.request.user, consumer=CONSUMER)
        return response


class PhonebookCreateView(LoginRequiredMixin, PhonebookFormMixin, CreateView):
    """Добавление справочника и заодно список всех — включая удалённые.

    Список живёт здесь, а не отдельной страницей: удалённый справочник
    пропадает из меню, и увидеть, кто его убрал, больше негде.
    """

    form_class = PhonebookCreateForm
    template_name = 'phonebook/add.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('phonebook', kwargs={'book_id': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        books = Phonebook.objects.select_related(
            'file_object__blob', 'created_by', 'updated_by', 'deleted_by',
        )
        context['books'] = [b for b in books if not b.is_deleted]
        context['deleted_books'] = [b for b in books if b.is_deleted]
        return context


class PhoneBook(LoginRequiredMixin, PhonebookFormMixin, UpdateView):
    """Страница справочника: просмотр файла и правка в модалке."""

    form_class = PhonebookForm
    template_name = 'phonebook/updatebook.html'
    pk_url_kwarg = 'book_id'
    context_object_name = 'books'

    def get_queryset(self):
        return Phonebook.objects.filter(is_deleted=False).select_related(
            'file_object__blob', 'created_by', 'updated_by',
        )

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('phonebook', kwargs={'book_id': self.object.pk})


class PhonebookViewFileView(LoginRequiredMixin, View):
    """Отдаёт файл справочника для просмотра в <iframe> (inline, не
    attachment). Тип, который небезопасно открывать в домене портала,
    StorageService сам переключит на скачивание."""

    def get(self, request, book_id):
        book = get_object_or_404(Phonebook, pk=book_id, is_deleted=False)
        if not book.file_object:
            raise Http404
        return StorageService.get_download_response(book.file_object, request, inline=True)


class PhonebookTrashView(TrashObjectView):
    model = Phonebook
    pk_kwarg = 'book_id'
    noun = 'справочник'


class PhonebookRestoreView(RestoreObjectView):
    model = Phonebook
    pk_kwarg = 'book_id'
    noun = 'справочник'
