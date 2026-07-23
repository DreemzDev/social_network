from django.contrib.auth.forms import UserCreationForm
from django import forms
from profiles.models import *

INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-4 py-2.5 text-sm '
    'text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:ring-blue-500 '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class RegisterUserForm(UserCreationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Придумайте логин',
        'autofocus': True,
    }))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Минимум 8 символов',
    }))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Повторите пароль',
    }))

    class Meta:
        model = get_user_model()
        fields = ('username', 'password1', 'password2')
