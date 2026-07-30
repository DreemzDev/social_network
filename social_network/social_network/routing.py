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
    "websocket": AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns
        )
    ),
})