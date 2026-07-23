from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model

User = get_user_model()

ACTIVITY_UPDATE_INTERVAL = timedelta(minutes=1)


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Обновляем активность не чаще раза в ACTIVITY_UPDATE_INTERVAL, чтобы не писать в БД на каждый запрос
        if request.user.is_authenticated:
            user = request.user
            now = timezone.now()
            if not user.last_activity or now - user.last_activity >= ACTIVITY_UPDATE_INTERVAL:
                user.last_activity = now
                user.save(update_fields=['last_activity'])

        response = self.get_response(request)
        return response