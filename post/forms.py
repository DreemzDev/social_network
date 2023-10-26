from django import forms
from.models import *




class AddPostForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cat'].empty_label = "Кто может видеть новость"
    
    class Meta:
        model = Post #связь формы с моделью post
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ['title', 'content','photo','cat']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Заголовок"}),
            'content': forms.Textarea(attrs={ 'cols':60, 'rows':1, 'placeholder': "Что у вас нового?"}),
            # 'photo': forms.ImageField(attrs={'class': 'input-file'})
            

        }
        