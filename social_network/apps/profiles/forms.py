from django import forms

from posts.models import Post
from.models import *

ADDPROFILE_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block mt-4'
ADDPROFILE_FIRST_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block'


class AddProfileForm(forms.ModelForm):

    class Meta:
        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('last_name', 'first_name','patronymic')
        widgets = {
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию", 'class': ADDPROFILE_FIRST_INPUT_CLASSES, 'autofocus': True}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя", 'class': ADDPROFILE_INPUT_CLASSES}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество", 'class': ADDPROFILE_INPUT_CLASSES}),

        }

SETTING_INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm '
    'text-gray-900 placeholder-gray-400 focus:border-[#0077FF] focus:ring-[#0077FF] '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class SettingProfileForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cat'].empty_label = "Выберите подразделение"

    class Meta:

        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('avatar','position','cat','rank','last_name', 'first_name','patronymic','birthday','phone_pts','phone_city','phone_zs','phone_9','phone_hc', 'cab')
        widgets = {
            'position': forms.TextInput(attrs={'placeholder': "Введите свою должность", 'class': SETTING_INPUT_CLASSES}),
            'rank': forms.TextInput(attrs={'placeholder': "Введите свое звание", 'class': SETTING_INPUT_CLASSES}),
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию", 'class': SETTING_INPUT_CLASSES}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя", 'class': SETTING_INPUT_CLASSES}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество", 'class': SETTING_INPUT_CLASSES}),
            'birthday': forms.DateInput(attrs={'type': 'date', 'class': SETTING_INPUT_CLASSES}, format='%Y-%m-%d'),
            'phone_pts': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': SETTING_INPUT_CLASSES}),
            'phone_city': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': SETTING_INPUT_CLASSES}),
            'phone_zs': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': SETTING_INPUT_CLASSES}),
            'phone_9': forms.TextInput(attrs={'type': 'tel', 'placeholder': "Введите номер телефона", 'class': SETTING_INPUT_CLASSES}),
            'phone_hc': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': SETTING_INPUT_CLASSES}),
            'cab': forms.TextInput(attrs={'placeholder': "Введите номер кабинета", 'class': SETTING_INPUT_CLASSES}),
            'cat': forms.Select(attrs={'class': SETTING_INPUT_CLASSES}),
            'avatar': forms.FileInput(attrs={'class': 'hidden', 'accept': 'image/*'}),
        }