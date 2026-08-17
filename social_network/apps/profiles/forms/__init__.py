"""Формы profiles по предметным областям — как модели и вьюхи рядом.

Пакет реэкспортирует все имена, поэтому `from profiles.forms import X`
снаружи работает как раньше.
"""
from .profile import (
    AddProfileForm, ChangePasswordForm, SecurityAnswerForm, SettingProfileForm,
    UserPhonesForm,
)
from .organizer import EventForm, NoteForm, TaskEditForm, TaskForm
from .auth import (
    LoginUserForm, PasswordResetRequestForm, RegisterUserForm, SetNewPasswordForm,
)

__all__ = [
    'AddProfileForm', 'ChangePasswordForm', 'SecurityAnswerForm',
    'SettingProfileForm', 'UserPhonesForm',
    'EventForm', 'NoteForm', 'TaskEditForm', 'TaskForm',
    'LoginUserForm', 'PasswordResetRequestForm', 'RegisterUserForm',
    'SetNewPasswordForm',
]
