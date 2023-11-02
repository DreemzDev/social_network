from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Message
from .forms import MessageForm, MessageUpdateForm
 
# @login_required
# def inbox(request):
#     messages = Message.objects.filter(recipient=request.user).order_by('-created_at')
#     return render(request, 'dialog/inbox.html', {'messages': messages})
 
# @login_required
# def sent(request):
#     messages = Message.objects.filter(sender=request.user).order_by('-created_at')
#     return render(request, 'dialog/sent.html', {'messages': messages})
 
# @login_required
# def compose(request):
#     if request.method == 'POST':
#         form = MessageForm(request.POST)
#         if form.is_valid():
#             message = form.save(commit=False)
#             message.sender = request.user
#             message.save()
#             return redirect('inbox')
#     else:
#         form = MessageForm()
#     return render(request, 'dialog/compose.html', {'form': form})
 
# @login_required
# def message(request, message_id):
#     message = Message.objects.get(id=message_id)
#     if request.method == 'POST':
#         form = MessageUpdateForm(request.POST, instance=message)
#         if form.is_valid():
#             form.save()
#             return redirect('inbox')
#     else:
#         form = MessageUpdateForm(instance=message)
#         return render(request, 'dialog/message.html', {'message': message, 'form': form})
 
# @login_required
# def delete(request, message_id):
#     message = Message.objects.get(id=message_id)
#     message.delete()
#     return redirect('inbox')