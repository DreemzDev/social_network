from django.shortcuts import render
from .models import Chat

def chat_list(request):
    chats = Chat.objects.filter(members=request.user)
    return render(request, 'chat/chat_list.html', {'chats': chats})

def chat_detail(request, chat_id):
    chat = Chat.objects.get(id=chat_id)
    return render(request, 'chat/chat_detail.html', {'chat': chat})
