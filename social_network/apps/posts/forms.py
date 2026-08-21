from django import forms

from.models import *


class AddPostForm(forms.ModelForm):
    
    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.fields['cat'].empty_label = "Кто может видеть новость"
    
    class Meta:
        model = Post #связь формы с моделью post
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автомат. заполняемых)
        fields = ['content']
        widgets = {

            'content': forms.Textarea(attrs={ 'rows':1, 'placeholder': "Что у вас нового?",'class': 'text-base pl-3 w-full rounded py-3 resize-none border-none outline-none focus:outline-none','oninput':"autoResize(this)"}),
        }


class PollForm(forms.Form):
    """Опрос приходит вместе с записью одной формой, поэтому и проверяется
    формой, а не в шаблоне и не в JS: JS-проверка — только удобство.

    Варианты приходят списком одноимённых полей (poll_options), которого у
    Django-формы нет, поэтому список разбирается в clean().
    """

    poll_multiple = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        raw = self.data.getlist('poll_options') if hasattr(self.data, 'getlist') else []
        options = [text.strip()[:200] for text in raw if text.strip()]

        if len(options) < Poll.MIN_OPTIONS:
            raise forms.ValidationError(
                f'В опросе должно быть не меньше {Poll.MIN_OPTIONS} вариантов'
            )
        if len(options) > Poll.MAX_OPTIONS:
            raise forms.ValidationError(
                f'В опросе не больше {Poll.MAX_OPTIONS} вариантов'
            )

        cleaned['options'] = options
        return cleaned

    def save(self, post):
        poll = Poll.objects.create(post=post, is_multiple=self.cleaned_data['poll_multiple'])
        PollOption.objects.bulk_create([
            PollOption(poll=poll, text=text, order=order)
            for order, text in enumerate(self.cleaned_data['options'])
        ])
        return poll
