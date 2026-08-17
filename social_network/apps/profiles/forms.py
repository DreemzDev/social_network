from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from posts.models import Post
from.models import *

ADDPROFILE_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block mt-4'
ADDPROFILE_FIRST_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block'


class AddProfileForm(forms.ModelForm):

    class Meta:
        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('last_name', 'first_name','patronymic')
        widgets = {
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию", 'class': ADDPROFILE_FIRST_INPUT_CLASSES, 'autofocus': True}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя", 'class': ADDPROFILE_INPUT_CLASSES}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество", 'class': ADDPROFILE_INPUT_CLASSES}),

        }

SETTING_INPUT_CLASSES = (
    'block w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2.5 text-base '
    'text-gray-900 placeholder-gray-400 focus:border-[#0077FF] focus:ring-[#0077FF] '
    'dark:border-zinc-600 dark:bg-zinc-700 dark:text-white dark:placeholder-gray-400'
)


class SettingProfileForm(forms.ModelForm):
    """Настройки профиля — то, что сотрудник знает про себя сам.

    Чего здесь намеренно нет: подразделения и большинства должностей. И то,
    и другое — место в структуре организации, а не самоописание; пока это
    были свободный текст и открытый выпадающий список, любой мог назначить
    себя начальником организации или перевести в другой отдел. Теперь
    структуру ведёт админ (Position, Category), а сотруднику остаются
    должности, у которых явно проставлен `assignable_by_user`.

    Звание — наоборот, свободный выбор из справочника: оно ничего не
    говорит о подчинении.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rank'].empty_label = "Выберите звание"

        # Своя должность остаётся в списке, даже если она не
        # самоназначаемая: иначе сохранение настроек молча снимало бы
        # человека с должности, которую ему назначил админ.
        available = Position.objects.filter(assignable_by_user=True)
        if self.instance.pk and self.instance.position_id:
            available = available | Position.objects.filter(pk=self.instance.position_id)

        if available.exists():
            self.fields['position'].queryset = available.distinct()
            self.fields['position'].empty_label = "Должность не выбрана"
        else:
            # Контрол либо работает, либо его нет в разметке: пустой список
            # должностей — это селект, который ничего не может.
            del self.fields['position']

    def clean_position(self):
        position = self.cleaned_data.get('position')
        if position and not position.has_free_slot(exclude_user=self.instance):
            raise ValidationError(
                f'Должность «{position}» уже занята '
                f'({position.max_holders} чел.) — обратитесь к администратору.'
            )
        return position

    class Meta:

        model = User #связь формы с моделью User
        # fields = '__all__' #fieds (какие поля нужно отобразить), __all__ (все поля кроме автоматю заполняемых)
        fields = ('avatar','cover','position','rank','last_name', 'first_name','patronymic','birthday', 'cab')
        widgets = {
            'last_name': forms.TextInput(attrs={'placeholder': "Введите свою фамилию", 'class': SETTING_INPUT_CLASSES}),
            'first_name': forms.TextInput(attrs={'placeholder': "Введите своё имя", 'class': SETTING_INPUT_CLASSES}),
            'patronymic': forms.TextInput(attrs={'placeholder': "Введите своё отчество", 'class': SETTING_INPUT_CLASSES}),
            'birthday': forms.DateInput(attrs={'type': 'date', 'class': SETTING_INPUT_CLASSES}, format='%Y-%m-%d'),
            'cab': forms.TextInput(attrs={'placeholder': "Введите номер кабинета", 'class': SETTING_INPUT_CLASSES}),
            'position': forms.Select(attrs={'class': SETTING_INPUT_CLASSES}),
            'rank': forms.Select(attrs={'class': SETTING_INPUT_CLASSES}),
            'avatar': forms.FileInput(attrs={'class': 'hidden', 'accept': 'image/*'}),
            'cover': forms.FileInput(attrs={'class': 'hidden', 'accept': 'image/*'}),
        }


class UserPhonesForm(forms.Form):
    """Телефоны сотрудника — по одному полю на каждый вид связи из
    справочника.

    Форма собирается из `PhoneType`, а не объявляется полями: раньше видов
    было ровно пять, они были вшиты в модель, в форму, в три шаблона и в
    маски ввода в base.html, и шестой стоил бы правки всех пяти мест.
    Теперь достаточно записи в админке.

    Не formset: у видов связи фиксированный список, добавлять и удалять
    строки не нужно — нужно заполнить те, что есть, и это ровно то, как
    страница выглядела раньше.
    """

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.phone_types = list(PhoneType.objects.all())
        existing = {phone.type_id: phone.number for phone in user.phones.all()} if user.pk else {}

        for phone_type in self.phone_types:
            self.fields[self.field_name(phone_type)] = forms.CharField(
                required=False,
                label=phone_type.name,
                initial=existing.get(phone_type.pk, ''),
                widget=forms.TextInput(attrs={
                    'placeholder': "Введите номер телефона",
                    'class': SETTING_INPUT_CLASSES,
                    # Маска — свойство вида связи, а не разметки: её
                    # применяет base.html по этому атрибуту.
                    'data-phone-mask': phone_type.mask,
                }),
            )

    @staticmethod
    def field_name(phone_type) -> str:
        return f'phone_{phone_type.pk}'

    def rows(self):
        """Пары (вид связи, поле) — шаблону нужно и то, и другое."""
        return [(phone_type, self[self.field_name(phone_type)]) for phone_type in self.phone_types]

    def save(self):
        """Пустое поле = номера нет: запись удаляется, а не хранится пустой.

        Иначе справочник телефонов постепенно заполнился бы пустыми
        строками, которые шаблонам пришлось бы отфильтровывать.
        """
        for phone_type in self.phone_types:
            number = (self.cleaned_data.get(self.field_name(phone_type)) or '').strip()
            if number:
                UserPhone.objects.update_or_create(
                    user=self.user, type=phone_type, defaults={'number': number},
                )
            else:
                UserPhone.objects.filter(user=self.user, type=phone_type).delete()


class ChangePasswordForm(forms.Form):
    old_password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Текущий пароль',
    }))
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Новый пароль',
        'id': 'setting-password-input',
    }))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': SETTING_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Повторите новый пароль',
    }))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data['old_password']
        if not self.user.check_password(old_password):
            raise ValidationError('Текущий пароль указан неверно.')
        return old_password

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('new_password1') and cleaned_data.get('new_password1') != cleaned_data.get('new_password2'):
            raise ValidationError('Пароли не совпадают.')
        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save(update_fields=['password'])
        return self.user


class SecurityAnswerForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('security_answer',)
        widgets = {
            'security_answer': forms.TextInput(attrs={
                'placeholder': "Введите проверочное слово",
                'class': SETTING_INPUT_CLASSES,
            }),
        }


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


# --- вход, регистрация и восстановление пароля ---------------------------
# Переехали из приложений login и register: каждое держало пустые models.py
# и каталог миграций ради одной формы.

AUTH_INPUT_CLASSES = 'intro-x login__input input input--lg border border-gray-300 block'
AUTH_INPUT_CLASSES_MT = AUTH_INPUT_CLASSES + ' mt-4'
AUTH_PASSWORD_CLASSES = AUTH_INPUT_CLASSES + ' pr-12'


class LoginUserForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Логин',
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Пароль',
    }))


class PasswordResetRequestForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Логин',
        'autofocus': True,
    }))
    security_answer = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' mt-4',
        'placeholder': 'Проверочное слово',
    }))

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username', '').strip()
        security_answer = cleaned_data.get('security_answer', '').strip()

        if username and security_answer:
            User = get_user_model()
            try:
                user = User.objects.get(username__iexact=username)
            except User.DoesNotExist:
                user = None

            if not user or not user.security_answer or user.security_answer.lower() != security_answer.lower():
                raise ValidationError('Логин или проверочное слово указаны неверно.')

            self.user = user

        return cleaned_data


class SetNewPasswordForm(forms.Form):
    new_password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12',
        'placeholder': 'Новый пароль',
        'id': 'password-input',
        'autofocus': True,
    }))
    new_password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_INPUT_CLASSES + ' pr-12 mt-4',
        'placeholder': 'Повторите новый пароль',
    }))

    def clean_new_password1(self):
        password = self.cleaned_data['new_password1']
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('new_password1') != cleaned_data.get('new_password2'):
            raise ValidationError('Пароли не совпадают.')
        return cleaned_data


class RegisterUserForm(UserCreationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES,
        'placeholder': 'Придумайте логин',
        'autofocus': True,
    }))
    last_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES_MT,
        'placeholder': 'Фамилия',
    }))
    first_name = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES_MT,
        'placeholder': 'Имя',
    }))
    patronymic = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES_MT,
        'placeholder': 'Отчество (необязательно)',
    }))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_PASSWORD_CLASSES,
        'placeholder': 'Минимум 8 символов',
        'id': 'password-input',
    }))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': AUTH_PASSWORD_CLASSES,
        'placeholder': 'Повторите пароль',
    }))
    security_answer = forms.CharField(widget=forms.TextInput(attrs={
        'class': AUTH_INPUT_CLASSES_MT,
        'placeholder': 'Например: имя первого питомца',
    }), help_text='Понадобится для восстановления доступа, если забудете пароль')

    class Meta:
        model = get_user_model()
        fields = ('username', 'last_name', 'first_name', 'patronymic', 'password1', 'password2', 'security_answer')

    def clean_security_answer(self):
        return self.cleaned_data['security_answer'].strip()
