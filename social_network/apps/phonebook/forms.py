from django import forms

from.models import *


class UpdateBookForm(forms.ModelForm):
    """Файл (book) больше не поле этой формы — он загружается отдельно через
    StorageService в PhoneBook.form_valid, т.к. ModelForm с FK на FileObject
    отрисовал бы выпадающий список файлов вместо <input type=file>."""

    class Meta:
        model = Phonebook
        fields = ['title']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Введите название справочника", 'class': "myfield"}),
        }