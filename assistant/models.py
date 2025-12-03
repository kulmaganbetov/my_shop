from django.db import models
from django.contrib.auth.models import User

class ChatSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_id = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Сессия чата'
        verbose_name_plural = 'Сессии чатов'
    
    def __str__(self):
        return f"Session {self.session_id}"


class ChatMessage(models.Model):
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    message = models.TextField(verbose_name='Сообщение')
    is_user = models.BooleanField(default=True, verbose_name='От пользователя')
    intent = models.CharField(max_length=50, blank=True, verbose_name='Намерение')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='Время')
    
    class Meta:
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'
        ordering = ['timestamp']
    
    def __str__(self):
        return f"{self.session.session_id} - {self.timestamp}"