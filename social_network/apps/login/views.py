
from django.shortcuts import redirect, render
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import logout, get_user_model
from django.views import View
from django.contrib import messages

from .forms import LoginUserForm, PasswordResetRequestForm, SetNewPasswordForm

RESET_SESSION_KEY = 'password_reset_user_id'


# Create your views here.
class LoginUser(LoginView):
    form_class = LoginUserForm
    template_name = 'login/login.html'

    def get_success_url(self):
        return reverse_lazy('home')

def logout_user(request):
    logout(request)
    return redirect('login')


class PasswordResetRequestView(View):
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
            User = get_user_model()
            user = User.objects.get(pk=user_id)
            user.set_password(form.cleaned_data['new_password1'])
            user.save(update_fields=['password'])
            del request.session[RESET_SESSION_KEY]
            messages.success(request, 'Пароль успешно изменён. Теперь можно войти.')
            return redirect('login')

        return render(request, self.template_name, {'form': form})
