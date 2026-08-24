"""Фоновая конвертация справочника в PDF.

В Celery, а не в вьюхе: конвертер (LibreOffice или сам Office) — отдельный
процесс, и на большом документе он думает секунды-десятки секунд; держать
на этом HTTP-запрос значит показывать пользователю зависшую страницу.
"""
import os
import tempfile

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.files import File

from storage.convert import ConversionError, convert_to_pdf
from storage.models import FileObject
from storage.services import StorageService

from .models import Phonebook

CONSUMER = 'phonebook.Phonebook:pdf'


@shared_task
def convert_phonebook_to_pdf(book_id, file_object_id, user_id=None):
    """Делает PDF-копию справочника и подставляет её как версию для просмотра.

    file_object_id — какой именно файл конвертируется. Без этой проверки
    замена справочника во время конвертации подсунула бы к новому файлу
    PDF от старого: задача узнала бы только id записи, а он не меняется.
    """
    book = Phonebook.objects.select_related('file_object__blob').filter(pk=book_id).first()
    if book is None or book.file_object_id != file_object_id:
        return 'skipped'

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    source = book.file_object

    with tempfile.TemporaryDirectory() as work_dir:
        try:
            pdf_path = convert_to_pdf(source.blob.file.path, source.original_name, work_dir)
        except ConversionError as error:
            Phonebook.objects.filter(pk=book.pk).update(
                conversion_status=Phonebook.Conversion.FAILED,
                conversion_error=str(error)[:200],
            )
            return 'failed'

        stem = os.path.splitext(source.original_name)[0]
        with open(pdf_path, 'rb') as produced:
            pdf_object = StorageService.upload(
                File(produced, name=f'{stem}.pdf'),
                user=user, category=FileObject.Category.CATALOG,
            )

    previous_pdf = book.pdf_file_object

    # Ещё одна проверка того же: конвертация шла минуты, за это время файл
    # могли заменить, и тогда результат уже не к чему прикладывать.
    updated = Phonebook.objects.filter(pk=book.pk, file_object_id=file_object_id).update(
        pdf_file_object=pdf_object,
        conversion_status=Phonebook.Conversion.DONE,
        conversion_error='',
    )
    if not updated:
        StorageService.detach(pdf_object, user=user, consumer=CONSUMER)
        return 'skipped'

    if previous_pdf:
        StorageService.detach(previous_pdf, user=user, consumer=CONSUMER)

    return 'done'
