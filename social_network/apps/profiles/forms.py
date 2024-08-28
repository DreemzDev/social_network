from django import forms

from posts.models import Post
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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cat'].empty_label = "Выберети подразделение"
    class Meta:
        
        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('avatar','position','cat','rank','last_name', 'first_name','patronymic','birthday','phone_pts','phone_city','phone_zs','phone_9','phone_hc', 'cab')
        widgets = {
            'position': forms.TextInput(attrs={'placeholder': "Введите свою должность", 'class': "myfield"}),
            'rank': forms.TextInput(attrs={'placeholder': "Введите свое звание", 'class': "myfield"}),
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию", 'class': "myfield"}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя", 'class': "myfield"}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество", 'class': "myfield"}),
            'birthday': forms.DateInput( attrs={'type':'date', 'class': "myfield"}, format='%Y-%m-%d'),
            'phone_pts': forms.TextInput(attrs={ 'placeholder': "Введите номер телефона", 'class': "myfield"}),
            'phone_city': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': "myfield"}),
            'phone_zs': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': "myfield"}),
            'phone_9': forms.TextInput(attrs={'type':'tel','placeholder': "Введите номер телефона", 'class': "myfield"}),
            'phone_hc': forms.TextInput(attrs={'placeholder': "Введите номер телефона", 'class': "myfield"}),
            'cab': forms.TextInput(attrs={'placeholder': "Введите номер кабинета", 'class': "myfield"}),
            'cat': forms.Select(attrs={ 'class': "myfield"}),
            

        }