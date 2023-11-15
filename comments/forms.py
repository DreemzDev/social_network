from django import forms
from .models import *


class CommentForm(forms.ModelForm):
    
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.fields['cat'].empty_label = "Кто может видеть новость"
    
    class Meta:
        model = Comment 
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        # fields = ['comment_author', 'posts','comment_pubdate','comment_text']
        fields = [ 'comment_text',]
        widgets = {
            'comment_text': forms.Textarea(attrs={ 'cols':60, 'rows':1, 'placeholder': "Написать комментарий..."}),

        }