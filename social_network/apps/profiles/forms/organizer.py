"""Формы личного органайзера: задачи, заметки, события."""
from django import forms

from ..models import Event, Note, Task


TASK_INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-base '
    'text-gray-900 placeholder-gray-400 focus:border-[#0077FF] focus:ring-[#0077FF] '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ('title', 'description', 'due_date')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Новая задача", 'class': TASK_INPUT_CLASSES}),
            'description': forms.Textarea(attrs={'placeholder': "Описание задачи...", 'class': TASK_INPUT_CLASSES, 'rows': 2}),
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': TASK_INPUT_CLASSES}),
        }


class TaskEditForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ('title', 'description', 'due_date')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Название задачи", 'class': TASK_INPUT_CLASSES}),
            'description': forms.Textarea(attrs={'placeholder': "Описание задачи...", 'class': TASK_INPUT_CLASSES, 'rows': 4}),
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': TASK_INPUT_CLASSES}),
        }


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ('content', 'color')
        widgets = {
            'content': forms.Textarea(attrs={'placeholder': "Текст заметки...", 'rows': 5}),
            'color': forms.Select(),
        }


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ('title', 'description', 'date', 'recurrence')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': "Название события", 'class': TASK_INPUT_CLASSES}),
            'description': forms.Textarea(attrs={'placeholder': "Описание события...", 'class': TASK_INPUT_CLASSES, 'rows': 4}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': TASK_INPUT_CLASSES}),
            'recurrence': forms.Select(attrs={'class': TASK_INPUT_CLASSES}),
        }
