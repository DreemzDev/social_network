from django import forms

from .models import NetworkAddress

FIELD_CLASS = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2.5 text-sm '
    'text-gray-900 focus:border-[#0077FF] focus:ring-[#0077FF] '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white'
)


class AddressForm(forms.ModelForm):
    """Только то, что заполняет человек. Данные обхода сюда не входят
    намеренно: их перезапишет следующий обход."""

    class Meta:
        model = NetworkAddress
        fields = ['name', 'kind', 'responsible', 'room', 'is_excluded', 'note']
        widgets = {
            'name': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Например, ПК бухгалтерии'}),
            'kind': forms.Select(attrs={'class': FIELD_CLASS}),
            'responsible': forms.Select(attrs={'class': FIELD_CLASS}),
            'room': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': '301'}),
            'note': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 3}),
        }
