"""Вход, выход, регистрация и восстановление пароля.

Жили двумя отдельными приложениями (`login` и `register`) — каждое с пустыми
models.py, admin.py и каталогом миграций ради четырёх вьюх. Это часть работы
с учётной записью, то есть profiles.

Восстановление пароля здесь не почтовое: портал внутренний, у сотрудников
может не быть рабочей почты, поэтому доступ возвращается по проверочному
слову (`User.security_answer`), а подтверждённый пользователь держится в
сессии между двумя шагами.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView

from ..forms import (
    LoginUserForm, PasswordResetRequestForm, RegisterUserForm, SetNewPasswordForm,
)

RESET_SESSION_KEY = 'password_reset_user_id'


class LoginUser(LoginView):
    form_class = LoginUserForm
    template_name = 'login/login.html'

    def get_success_url(self):
        return reverse_lazy('home')


def logout_user(request):
    logout(request)
    return redirect('login')


class RegisterUser(CreateView):
    form_class = RegisterUserForm
    template_name = 'register/register.html'
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        login(self.request, form.save())
        return redirect('home')


class PasswordResetRequestView(View):
    """Шаг 1: логин плюс проверочное слово."""

    template_name = 'login/password_reset_request.html'

    def get(self, request):
        return render(request, self.template_name, {'form': PasswordResetRequestForm()})

    def post(self, request):
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            request.session[RESET_SESSION_KEY] = form.user.id
            return redirect('password_reset_confirm')
        return render(request, self.template_name, {'form': form})


class PasswordResetConfirmView(View):
    """Шаг 2: новый пароль. Без пометки в сессии шаг недоступен — иначе
    форму смены пароля открывал бы кто угодно по прямой ссылке."""

    template_name = 'login/password_reset_confirm.html'

    def get(self, request):
        if not request.session.get(RESET_SESSION_KEY):
            return redirect('password_reset_request')
        return render(request, self.template_name, {'form': SetNewPasswordForm()})

    def post(self, request):
        user_id = request.session.get(RESET_SESSION_KEY)
        if not user_id:
            return redirect('password_reset_request')

        form = SetNewPasswordForm(request.POST)
        if form.is_valid():
            user = get_user_model().objects.get(pk=user_id)
            user.set_password(form.cleaned_data['new_password1'])
            user.save(update_fields=['password'])
            del request.session[RESET_SESSION_KEY]
            messages.success(request, 'Пароль успешно изменён. Теперь можно войти.')
            return redirect('login')

        return render(request, self.template_name, {'form': form})
