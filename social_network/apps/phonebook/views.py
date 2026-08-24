"""Справочники организации — документы, которые сотрудник открывает читать.

Файл хранится через storage и показывается в <iframe>. Править может любой
сотрудник: ответственность здесь даёт подпись (created_by/updated_by), а не
запрет. Удаление мягкое — базовыми вьюхами storage.fmviews.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, View

from storage import limits
from storage.convert import converter_available
from storage.exceptions import FileTooLargeError, QuotaExceededError
from storage.fmviews import RestoreObjectView, TrashObjectView
from storage.models import FileObject
from storage.services import StorageService

from .forms import PhonebookCreateForm, PhonebookForm
from .models import Phonebook
from .tasks import convert_phonebook_to_pdf

CONSUMER = 'phonebook.Phonebook'


def queue_conversion(book, user, save=False):
    """Ставит справочник в очередь на PDF-копию.

    on_commit, а не сразу: воркер читает запись из БД и до фиксации
    транзакции не увидел бы ни нового файла, ни статуса «готовится».
    """
    if save:
        Phonebook.objects.filter(pk=book.pk).update(
            conversion_status=Phonebook.Conversion.PENDING, conversion_error='',
        )

    file_object_id = book.file_object_id
    transaction.on_commit(
        lambda: convert_phonebook_to_pdf.delay(book.pk, file_object_id, user.pk if user else None)
    )


class PhonebookFormMixin:
    """Приём файла, общий для добавления и правки справочника."""

    model = Phonebook

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['max_upload_size'] = limits.max_upload_size()
        # Кнопка «Показать в браузере» не должна появляться там, где
        # конвертировать нечем (ARCHITECTURE.md, 12.4). Проверка по
        # конкретному файлу: Word на машине может стоять, а Excel — нет.
        book = getattr(self, 'object', None)
        context['converter_available'] = converter_available(
            book.file_object.original_name if book and book.file_object else None
        )
        return context

    def form_valid(self, form):
        uploaded = form.cleaned_data.get('book')
        old_file_object = form.instance.file_object
        old_pdf_object = form.instance.pdf_file_object

        if uploaded:
            try:
                form.instance.file_object = StorageService.upload(
                    uploaded, user=self.request.user, category=FileObject.Category.CATALOG,
                )
            except (FileTooLargeError, QuotaExceededError) as e:
                form.add_error('book', str(e))
                return self.form_invalid(form)

            # PDF-копия относилась к прежнему файлу: оставить её значило бы
            # показывать в браузере старый справочник под новым названием.
            form.instance.pdf_file_object = None
            form.instance.conversion_error = ''
            form.instance.conversion_status = (
                Phonebook.Conversion.PENDING
                if form.instance.needs_conversion
                and converter_available(form.instance.file_object.original_name)
                else Phonebook.Conversion.NONE
            )

        response = super().form_valid(form)

        # Отвязка только после успешного сохранения: detach уводит blob в
        # ORPHAN, и упади сохранение между делом — запись осталась бы
        # ссылаться на файл, помеченный к удалению.
        if uploaded and old_file_object and old_file_object != form.instance.file_object:
            StorageService.detach(old_file_object, user=self.request.user, consumer=CONSUMER)
        if uploaded and old_pdf_object:
            StorageService.detach(old_pdf_object, user=self.request.user, consumer=CONSUMER)

        if uploaded and form.instance.conversion_status == Phonebook.Conversion.PENDING:
            queue_conversion(form.instance, self.request.user)
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
    StorageService сам переключит на скачивание.

    По умолчанию отдаётся то, что покажет браузер (PDF-копия, если она
    сделана), а ?original=1 — исходный файл: кнопка «Скачать» обязана
    отдавать именно загруженный документ, а не его пересборку.
    """

    def get(self, request, book_id):
        book = get_object_or_404(
            Phonebook.objects.select_related('file_object__blob', 'pdf_file_object__blob'),
            pk=book_id, is_deleted=False,
        )
        if request.GET.get('original') == '1':
            file_object = book.file_object
        else:
            file_object = book.preview_object or book.file_object
        if not file_object:
            raise Http404
        return StorageService.get_download_response(file_object, request, inline=True)


class PhonebookConvertView(LoginRequiredMixin, View):
    """Запускает подготовку PDF-копии по кнопке — для справочников,
    загруженных до появления конвертации, и для повторной попытки после
    отказа. Справочник правит любой сотрудник, поэтому и конвертировать
    может любой (см. докстринг модуля)."""

    def post(self, request, book_id):
        book = get_object_or_404(
            Phonebook.objects.select_related('file_object__blob'), pk=book_id, is_deleted=False,
        )

        if not book.needs_conversion:
            return JsonResponse(
                {'success': False, 'error': 'Этот справочник и так открывается в браузере'},
                status=400,
            )
        if not converter_available(book.file_object.original_name):
            return JsonResponse(
                {'success': False,
                 'error': 'На сервере нет программы, которая открывает такой формат'},
                status=503,
            )

        queue_conversion(book, request.user, save=True)
        return JsonResponse({'success': True, 'status': Phonebook.Conversion.PENDING})


class PhonebookConversionStatusView(LoginRequiredMixin, View):
    """Готова ли PDF-копия. Страница справочника опрашивает этот адрес, пока
    идёт конвертация: результата ждёт человек, смотрящий на пустую рамку."""

    def get(self, request, book_id):
        book = get_object_or_404(
            Phonebook.objects.select_related('file_object__blob', 'pdf_file_object__blob'),
            pk=book_id, is_deleted=False,
        )
        return JsonResponse({
            'status': book.conversion_status,
            'previewable': book.is_previewable,
            'error': book.conversion_error,
        })


class PhonebookTrashView(TrashObjectView):
    model = Phonebook
    pk_kwarg = 'book_id'
    noun = 'справочник'


class PhonebookRestoreView(RestoreObjectView):
    model = Phonebook
    pk_kwarg = 'book_id'
    noun = 'справочник'
