from django import forms

from.models import *


class UpdateBookForm(forms.ModelForm):
    
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.fields['cat'].empty_label = "Кто может видеть новость"
    
    class Meta:
        model = Phonebook #связь формы с моделью phonebook
        fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        # fields = [ 'title','book']
        widgets = {
            
            'title': forms.TextInput(attrs={  'placeholder': "Введите название справочника", 'class': "myfield"}),
           
            

        }