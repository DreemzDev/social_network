from django.db import models
from django.contrib.auth import get_user_model 

# Create your models here.
class Message(models.Model):
    sender = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='received_messages')
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
 
    # def __str__(self):
    #     return f'{self.sender.username} -> {self.recipient.username}'