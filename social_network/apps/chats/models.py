from django.db import models
from django.contrib.auth import get_user_model

class Chat(models.Model):
    members = models.ManyToManyField(get_user_model())

class Message(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE)
    sender = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
