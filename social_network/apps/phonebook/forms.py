from django import forms

from .models import Phonebook

# Что принимаем в справочник. Список свой, а не storage'а: обменник берёт
# любой файл, а справочник — документ, который сотрудник идёт читать.
# Расширение проверяется здесь, а не в JS: атрибут accept у <input> —
# подсказка проводнику, а не запрет (CLAUDE.md, «Формы и валидация»).
ALLOWED_EXTENSIONS = {
    'pdf',
    'doc', 'docx', 'odt', 'rtf',
    'xls', 'xlsx', 'ods',
    'ppt', 'pptx', 'odp',
}


class PhonebookForm(forms.ModelForm):
    """Название и файл справочника.

    Файл — поле формы, а не модели: в модели там FK на FileObject, и
    ModelForm нарисовал бы выпадающий список всех файлов портала вместо
    <input type=file>. Сохраняет его вьюха через StorageService.
    """

    book = forms.FileField(
        required=False, label='Файл справочника',
        widget=forms.ClearableFileInput(attrs={
            'class': 'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2.5 '
                     'text-sm text-gray-900 focus:border-[#0077FF] focus:ring-[#0077FF] '
                     'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white',
            'accept': '.' + ',.'.join(sorted(ALLOWED_EXTENSIONS)),
        }),
    )

    class Meta:
        model = Phonebook
        fields = ['title']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': "Введите название справочника",
                'class': 'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2.5 '
                         'text-sm text-gray-900 focus:border-[#0077FF] focus:ring-[#0077FF] '
                         'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white',
            }),
        }

    def clean_book(self):
        book = self.cleaned_data.get('book')
        if not book:
            return book

        _, dot, extension = book.name.rpartition('.')
        if not dot or extension.lower() not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                'Такой файл в справочник загрузить нельзя. Подойдут: '
                + ', '.join(sorted(e.upper() for e in ALLOWED_EXTENSIONS)) + '.'
            )
        return book


class PhonebookCreateForm(PhonebookForm):
    """То же, но файл обязателен: справочник без файла — пустой пункт меню."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['book'].required = True
