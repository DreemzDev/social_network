from django.contrib.auth.forms import AuthenticationForm
from django import forms

INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-4 py-2.5 text-sm '
    'text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:ring-blue-500 '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class LoginUserForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Логин',
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Пароль',
    }))
