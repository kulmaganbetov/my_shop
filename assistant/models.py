from django.db import models
from django.contrib.auth.models import User


class ChatSession(models.Model):
    """Сессия чата с клиентом"""

    STATUS_CHOICES = [
        ('active', 'Активный (бот)'),
        ('pending_manager', 'Ожидает менеджера'),
        ('with_manager', 'С менеджером'),
        ('closed', 'Закрыт'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='chat_sessions', verbose_name='Пользователь')
    session_id = models.CharField(max_length=100, unique=True, verbose_name='ID сессии')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active',
                              verbose_name='Статус')
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='managed_sessions', verbose_name='Менеджер')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')
    client_name = models.CharField(max_length=100, blank=True, verbose_name='Имя клиента')
    client_phone = models.CharField(max_length=20, blank=True, verbose_name='Телефон клиента')

    class Meta:
        verbose_name = 'Сессия чата'
        verbose_name_plural = 'Сессии чатов'
        ordering = ['-updated_at']

    def __str__(self):
        return f"Session {self.session_id[:8]}... ({self.get_status_display()})"

    @property
    def needs_attention(self):
        """Требует внимания менеджера"""
        return self.status == 'pending_manager'


class ChatMessage(models.Model):
    """Сообщение в чате"""

    SENDER_CHOICES = [
        ('user', 'Клиент'),
        ('bot', 'Бот'),
        ('manager', 'Менеджер'),
    ]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE,
                                related_name='messages', verbose_name='Сессия')
    message = models.TextField(verbose_name='Сообщение')
    is_user = models.BooleanField(default=True, verbose_name='От пользователя')
    sender_type = models.CharField(max_length=10, choices=SENDER_CHOICES, default='user',
                                   verbose_name='Отправитель')
    intent = models.CharField(max_length=50, blank=True, verbose_name='Намерение')
    attachment = models.FileField(upload_to='chat_attachments/', null=True, blank=True,
                                  verbose_name='Вложение')
    attachment_type = models.CharField(max_length=50, blank=True, verbose_name='Тип вложения')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='Время')

    class Meta:
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.get_sender_type_display()}: {self.message[:50]}..."

    def save(self, *args, **kwargs):
        # Автоматически устанавливаем sender_type на основе is_user
        if self.is_user:
            self.sender_type = 'user'
        elif self.sender_type == 'user':
            self.sender_type = 'bot'
        super().save(*args, **kwargs)


class AssistantLog(models.Model):
    """Логирование работы ассистента"""

    LOG_TYPE_CHOICES = [
        ('user_question', 'Вопрос пользователя'),
        ('bot_response', 'Ответ бота'),
        ('error', 'Ошибка'),
        ('timeout', 'Таймаут'),
        ('api_error', 'Ошибка API'),
        ('manager_handoff', 'Передача менеджеру'),
        ('manager_response', 'Ответ менеджера'),
        ('session_start', 'Начало сессии'),
        ('session_end', 'Конец сессии'),
    ]

    SEVERITY_CHOICES = [
        ('info', 'Информация'),
        ('warning', 'Предупреждение'),
        ('error', 'Ошибка'),
        ('critical', 'Критическая ошибка'),
    ]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='logs', verbose_name='Сессия')
    log_type = models.CharField(max_length=20, choices=LOG_TYPE_CHOICES, verbose_name='Тип лога')
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='info',
                                verbose_name='Важность')
    message = models.TextField(verbose_name='Сообщение')
    user_input = models.TextField(blank=True, verbose_name='Ввод пользователя')
    bot_output = models.TextField(blank=True, verbose_name='Ответ бота')
    intent = models.CharField(max_length=50, blank=True, verbose_name='Определенное намерение')
    error_details = models.TextField(blank=True, verbose_name='Детали ошибки')
    handoff_reason = models.CharField(max_length=200, blank=True, verbose_name='Причина передачи')
    response_time_ms = models.IntegerField(null=True, blank=True, verbose_name='Время ответа (мс)')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='Время')
    extra_data = models.JSONField(null=True, blank=True, verbose_name='Дополнительные данные')

    class Meta:
        verbose_name = 'Лог ассистента'
        verbose_name_plural = 'Логи ассистента'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['log_type', 'timestamp']),
            models.Index(fields=['severity', 'timestamp']),
            models.Index(fields=['session', 'timestamp']),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.get_log_type_display()} - {self.timestamp}"
