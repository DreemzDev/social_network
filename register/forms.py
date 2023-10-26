from django.contrib.auth.forms import UserCreationForm

from profiles.models import User







class RegisterUserForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'password1', 'password2')