from django import forms

from post.models import Post
from.models import *




class AddProfileForm(forms.ModelForm):

    
    class Meta:
        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('last_name', 'first_name','patronymic')
        widgets = {
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию"}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя"}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество"}),

        }

class SettingProfileForm(forms.ModelForm):
    class Meta:
        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('position','rank','last_name', 'first_name','patronymic','birth_date','phone')
        widgets = {
            'position': forms.TextInput(attrs={'placeholder': "Введите свою должность"}),
            'rank': forms.TextInput(attrs={'placeholder': "Введите свой ранг"}),
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию"}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя"}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество"}),
            'birth_date': forms.DateInput(attrs={'placeholder': "Введите дату рождения: xx.xx.xx"}),
            'phone': forms.TextInput(attrs={'placeholder': "Введите номер телефона"}),

        }
