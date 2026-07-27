from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

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
        fields = ('avatar','cover','position','cat','rank','last_name', 'first_name','patronymic','birthday','phone_pts','phone_city','phone_zs','phone_9','phone_hc', 'cab')
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
            'cover': forms.FileInput(attrs={'class': 'hidden', 'accept': 'image/*'}),
        }


class ChangePasswordForm(forms.Form):
    old_password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Текущий пароль',
    }))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Новый пароль',
        'id': 'setting-password-input',
    }))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Повторите новый пароль',
    }))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data['old_password']
        if not self.user.check_password(old_password):
            raise ValidationError('Текущий пароль указан неверно.')
        return old_password

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('new_password1') and cleaned_data.get('new_password1') != cleaned_data.get('new_password2'):
            raise ValidationError('Пароли не совпадают.')
        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save(update_fields=['password'])
        return self.user


class SecurityAnswerForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('security_answer',)
        widgets = {
            'security_answer': forms.TextInput(attrs={
                'placeholder': "Введите проверочное слово",
                'class': SETTING_INPUT_CLASSES,
            }),
        }


TASK_INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-base '
    'text-gray-900 placeholder-gray-400 focus:border-[#0077FF] focus:ring-[#0077FF] '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ('title', 'due_date')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Новая задача", 'class': TASK_INPUT_CLASSES}),
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': TASK_INPUT_CLASSES}),
        }


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ('content',)
        widgets = {
            'content': forms.Textarea(attrs={'class': TASK_INPUT_CLASSES, 'rows': 8, 'placeholder': "Ваши заметки..."}),
        }


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ('title', 'date')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Название события", 'class': TASK_INPUT_CLASSES}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': TASK_INPUT_CLASSES}),
        }