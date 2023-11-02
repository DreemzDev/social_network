from django import forms
from .models import Message
 
class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['recipient', 'text']
 
class MessageUpdateForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['read_at']