"""Вход, регистрация и восстановление пароля.

Восстановление не почтовое: портал внутренний, доступ возвращается по
проверочному слову (User.security_answer).
"""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


AUTH_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block'
AUTH_PASSWORD_CLASSES = AUTH_INPUT_CLASSES + ' pr-12'


class LoginUserForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Логин',
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Пароль',
    }))


class PasswordResetRequestForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Логин',
        'autofocus': True,
    }))
    security_answer = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' mt-4',
        'placeholder': 'Проверочное слово',
    }))

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username', '').strip()
        security_answer = cleaned_data.get('security_answer', '').strip()

        if username and security_answer:
            User = get_user_model()
            try:
                user = User.objects.get(username__iexact=username)
            except User.DoesNotExist:
                user = None

            if not user or not user.security_answer or user.security_answer.lower() != security_answer.lower():
                raise ValidationError('Логин или проверочное слово указаны неверно.')

            self.user = user

        return cleaned_data


class SetNewPasswordForm(forms.Form):
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Новый пароль',
        'id': 'password-input',
        'autofocus': True,
    }))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12 mt-4',
        'placeholder': 'Повторите новый пароль',
    }))

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('new_password1') != cleaned_data.get('new_password2'):
            raise ValidationError('Пароли не совпадают.')
        return cleaned_data


class RegisterUserForm(UserCreationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Придумайте логин',
        'autofocus': True,
    }))
    last_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Фамилия',
    }))
    first_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Имя',
    }))
    patronymic = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Отчество (необязательно)',
    }))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_PASSWORD_CLASSES,
        'placeholder': 'Минимум 8 символов',
        'id': 'password-input',
    }))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_PASSWORD_CLASSES,
        'placeholder': 'Повторите пароль',
    }))
    security_answer = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Например: имя первого питомца',
    }), help_text='Понадобится для восстановления доступа, если забудете пароль')

    class Meta:
        model = get_user_model()
        fields = ('username', 'last_name', 'first_name', 'patronymic', 'password1', 'password2', 'security_answer')

    def clean_security_answer(self):
        return self.cleaned_data['security_answer'].strip()
