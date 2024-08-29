from django.urls import path
from .views import  chat_list, chat_detail

from django.urls import path
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from chats.consumers import ChatConsumer

urlpatterns = [
    path('chats/', chat_list, name='chat_list'),
    path('chats/<int:chat_id>/', chat_detail, name='chat_detail'),
]
