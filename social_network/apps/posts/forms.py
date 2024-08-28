from django import forms

from.models import *


class AddPostForm(forms.ModelForm):
    
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.fields['cat'].empty_label = "Кто может видеть новость"
    
    class Meta:
        model = Post #связь формы с моделью post
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автомат. заполняемых)
        fields = [ 'content','photo']
        widgets = {
            
            'content': forms.Textarea(attrs={ 'cols':60, 'rows':1, 'placeholder': "Что у вас нового?"}),
        }

