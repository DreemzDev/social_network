"""
ASGI config for social_network project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "social_network.settings")
django_asgi_app = get_asgi_application()
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import re_path
from profiles.consumers import ExtendedChatConsumer
from comments.consumers import PostCommentsConsumer

websocket_urlpatterns = [
    re_path(r'^chat_ws$', ExtendedChatConsumer.as_asgi()),
    re_path(r'^ws/post/(?P<post_id>\d+)/comments/$', PostCommentsConsumer.as_asgi()),
]

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})