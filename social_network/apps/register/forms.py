from django.contrib.auth.forms import UserCreationForm
from django import forms
from profiles.models import *







class RegisterUserForm(UserCreationForm):
    # username = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-input','placeholder': "Логин"}))
    # password1 = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': "Введите пароль"}))
    # password2 = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': "Повторите пароль"}))
    class Meta:
        model = get_user_model()
        fields = ('username', 'password1', 'password2')