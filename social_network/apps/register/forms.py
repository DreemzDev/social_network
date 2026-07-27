from django.contrib.auth.forms import UserCreationForm
from django import forms
from profiles.models import *

INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block mt-4'
FIRST_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block'
PASSWORD_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block pr-12'


class RegisterUserForm(UserCreationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': FIRST_INPUT_CLASSES,
        'placeholder': 'Придумайте логин',
        'autofocus': True,
    }))
    last_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Фамилия',
    }))
    first_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Имя',
    }))
    patronymic = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Отчество (необязательно)',
    }))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': PASSWORD_INPUT_CLASSES,
        'placeholder': 'Минимум 8 символов',
        'id': 'password-input',
    }))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': PASSWORD_INPUT_CLASSES,
        'placeholder': 'Повторите пароль',
    }))
    security_answer = forms.CharField(widget=forms.TextInput(attrs={
        'class': INPUT_CLASSES,
        'placeholder': 'Например: имя первого питомца',
    }), help_text='Понадобится для восстановления доступа, если забудете пароль')

    class Meta:
        model = get_user_model()
        fields = ('username', 'last_name', 'first_name', 'patronymic', 'password1', 'password2', 'security_answer')

    def clean_security_answer(self):
        return self.cleaned_data['security_answer'].strip()
