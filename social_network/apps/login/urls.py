from django.urls import path

from .views import*

urlpatterns = [
    path ('login/', LoginUser.as_view(), name='login'),
    path ('logout/', logout_user, name='logout'),
    path ('password-reset/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path ('password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
