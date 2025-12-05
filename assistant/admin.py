from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.admin import SimpleListFilter

from .models import ChatSession, ChatMessage, AssistantLog


class StatusFilter(SimpleListFilter):
    """Фильтр по статусу сессии"""
    title = 'Статус'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return ChatSession.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class NeedsAttentionFilter(SimpleListFilter):
    """Фильтр: требует внимания менеджера"""
    title = 'Требует внимания'
    parameter_name = 'needs_attention'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Да'),
            ('no', 'Нет'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(status='pending_manager')
        elif self.value() == 'no':
            return queryset.exclude(status='pending_manager')
        return queryset


class ChatMessageInline(admin.TabularInline):
    """Inline отображение сообщений в сессии"""
    model = ChatMessage
    extra = 0
    readonly_fields = ['sender_type', 'message', 'intent', 'timestamp', 'attachment_preview']
    fields = ['sender_type', 'message', 'intent', 'timestamp', 'attachment_preview']
    ordering = ['-timestamp']
    max_num = 50

    def attachment_preview(self, obj):
        if obj.attachment:
            return format_html('<a href="{}" target="_blank">Скачать</a>', obj.attachment.url)
        return '-'
    attachment_preview.short_description = 'Вложение'

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    """Админка для сессий чата"""
    list_display = ['session_short', 'status_badge', 'client_info', 'manager',
                    'messages_count', 'created_at', 'updated_at', 'chat_action']
    list_filter = [NeedsAttentionFilter, StatusFilter, 'created_at', 'manager']
    search_fields = ['session_id', 'client_name', 'client_phone']
    readonly_fields = ['session_id', 'created_at', 'updated_at']
    list_per_page = 25
    ordering = ['-updated_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Информация о сессии', {
            'fields': ('session_id', 'status', 'manager')
        }),
        ('Информация о клиенте', {
            'fields': ('user', 'client_name', 'client_phone'),
            'classes': ('collapse',)
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    inlines = [ChatMessageInline]

    def session_short(self, obj):
        return obj.session_id[:12] + '...'
    session_short.short_description = 'ID сессии'

    def status_badge(self, obj):
        colors = {
            'active': 'green',
            'pending_manager': 'orange',
            'with_manager': 'blue',
            'closed': 'gray',
        }
        icons = {
            'active': '🤖',
            'pending_manager': '🔔',
            'with_manager': '👨‍💼',
            'closed': '✅',
        }
        color = colors.get(obj.status, 'gray')
        icon = icons.get(obj.status, '')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color, icon, obj.get_status_display()
        )
    status_badge.short_description = 'Статус'
    status_badge.admin_order_field = 'status'

    def client_info(self, obj):
        name = obj.client_name or 'Неизвестен'
        phone = obj.client_phone or '-'
        return format_html('<strong>{}</strong><br><small>{}</small>', name, phone)
    client_info.short_description = 'Клиент'

    def messages_count(self, obj):
        count = obj.messages.count()
        return format_html('<span class="badge badge-info">{}</span>', count)
    messages_count.short_description = 'Сообщений'

    def chat_action(self, obj):
        if obj.status in ['pending_manager', 'with_manager']:
            url = reverse('admin:assistant_chatsession_chat', args=[obj.pk])
            return format_html(
                '<a class="button" href="{}" style="background: #417690; color: white; padding: 5px 10px; '
                'border-radius: 4px; text-decoration: none;">💬 Открыть чат</a>',
                url
            )
        return '-'
    chat_action.short_description = 'Действия'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:session_id>/chat/', self.admin_site.admin_view(self.chat_view),
                 name='assistant_chatsession_chat'),
            path('<int:session_id>/send-message/', self.admin_site.admin_view(self.send_message_view),
                 name='assistant_chatsession_send_message'),
            path('<int:session_id>/messages/', self.admin_site.admin_view(self.get_messages_view),
                 name='assistant_chatsession_messages'),
        ]
        return custom_urls + urls

    def chat_view(self, request, session_id):
        """Страница чата с клиентом для менеджера"""
        session = get_object_or_404(ChatSession, pk=session_id)

        # Если статус pending_manager, меняем на with_manager и назначаем менеджера
        if session.status == 'pending_manager':
            session.status = 'with_manager'
            session.manager = request.user
            session.save()

            # Логируем
            AssistantLog.objects.create(
                session=session,
                log_type='manager_handoff',
                severity='info',
                message=f'Менеджер {request.user.username} взял сессию',
                handoff_reason='Менеджер принял диалог'
            )

        chat_messages = session.messages.all().order_by('timestamp')

        context = {
            **self.admin_site.each_context(request),
            'session': session,
            'chat_messages': chat_messages,
            'title': f'Чат с клиентом - {session.client_name or session.session_id[:8]}',
        }
        return render(request, 'admin/assistant/chatsession/chat.html', context)

    def send_message_view(self, request, session_id):
        """API для отправки сообщения менеджером"""
        if request.method != 'POST':
            return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

        session = get_object_or_404(ChatSession, pk=session_id)
        message_text = request.POST.get('message', '').strip()

        if not message_text:
            return JsonResponse({'success': False, 'error': 'Сообщение не может быть пустым'})

        # Создаем сообщение от менеджера
        ChatMessage.objects.create(
            session=session,
            message=message_text,
            is_user=False,
            sender_type='manager'
        )

        # Логируем
        AssistantLog.objects.create(
            session=session,
            log_type='manager_response',
            severity='info',
            message='Менеджер отправил сообщение',
            bot_output=message_text
        )

        # Обновляем время сессии
        session.save()

        return JsonResponse({'success': True})

    def get_messages_view(self, request, session_id):
        """API для получения сообщений (для обновления чата)"""
        session = get_object_or_404(ChatSession, pk=session_id)
        last_id = request.GET.get('last_id', 0)

        messages_qs = session.messages.filter(pk__gt=last_id).order_by('timestamp')
        messages_data = []

        for msg in messages_qs:
            messages_data.append({
                'id': msg.pk,
                'message': msg.message,
                'sender_type': msg.sender_type,
                'timestamp': msg.timestamp.strftime('%H:%M'),
            })

        return JsonResponse({'success': True, 'messages': messages_data})

    actions = ['mark_as_closed', 'assign_to_me']

    @admin.action(description='Закрыть выбранные сессии')
    def mark_as_closed(self, request, queryset):
        updated = queryset.update(status='closed')
        self.message_user(request, f'{updated} сессий закрыто.', messages.SUCCESS)

    @admin.action(description='Назначить себе')
    def assign_to_me(self, request, queryset):
        updated = queryset.filter(status='pending_manager').update(
            status='with_manager',
            manager=request.user
        )
        self.message_user(request, f'{updated} сессий назначено вам.', messages.SUCCESS)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    """Админка для сообщений чата"""
    list_display = ['session_link', 'sender_badge', 'message_short', 'intent', 'timestamp']
    list_filter = ['sender_type', 'intent', 'timestamp']
    search_fields = ['message', 'session__session_id']
    readonly_fields = ['session', 'message', 'is_user', 'sender_type', 'intent',
                       'attachment', 'attachment_type', 'timestamp']
    list_per_page = 50
    ordering = ['-timestamp']
    date_hierarchy = 'timestamp'

    def session_link(self, obj):
        url = reverse('admin:assistant_chatsession_change', args=[obj.session.pk])
        return format_html('<a href="{}">{}</a>', url, obj.session.session_id[:12] + '...')
    session_link.short_description = 'Сессия'

    def sender_badge(self, obj):
        colors = {
            'user': '#4CAF50',
            'bot': '#2196F3',
            'manager': '#FF9800',
        }
        icons = {
            'user': '👤',
            'bot': '🤖',
            'manager': '👨‍💼',
        }
        color = colors.get(obj.sender_type, 'gray')
        icon = icons.get(obj.sender_type, '')
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">'
            '{} {}</span>',
            color, icon, obj.get_sender_type_display()
        )
    sender_badge.short_description = 'Отправитель'

    def message_short(self, obj):
        text = obj.message[:100] + '...' if len(obj.message) > 100 else obj.message
        return text
    message_short.short_description = 'Сообщение'

    def has_add_permission(self, request):
        return False


@admin.register(AssistantLog)
class AssistantLogAdmin(admin.ModelAdmin):
    """Админка для логов ассистента"""
    list_display = ['timestamp', 'severity_badge', 'log_type_badge', 'session_link',
                    'message_short', 'response_time']
    list_filter = ['severity', 'log_type', 'timestamp']
    search_fields = ['message', 'user_input', 'bot_output', 'error_details',
                     'session__session_id']
    readonly_fields = ['session', 'log_type', 'severity', 'message', 'user_input',
                       'bot_output', 'intent', 'error_details', 'handoff_reason',
                       'response_time_ms', 'timestamp', 'extra_data']
    list_per_page = 100
    ordering = ['-timestamp']
    date_hierarchy = 'timestamp'

    fieldsets = (
        ('Основное', {
            'fields': ('log_type', 'severity', 'message', 'timestamp')
        }),
        ('Данные диалога', {
            'fields': ('session', 'user_input', 'bot_output', 'intent'),
            'classes': ('collapse',)
        }),
        ('Ошибки и передача', {
            'fields': ('error_details', 'handoff_reason'),
            'classes': ('collapse',)
        }),
        ('Метрики', {
            'fields': ('response_time_ms', 'extra_data'),
            'classes': ('collapse',)
        }),
    )

    def severity_badge(self, obj):
        colors = {
            'info': '#4CAF50',
            'warning': '#FF9800',
            'error': '#f44336',
            'critical': '#9C27B0',
        }
        color = colors.get(obj.severity, 'gray')
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 4px;">{}</span>',
            color, obj.get_severity_display()
        )
    severity_badge.short_description = 'Важность'
    severity_badge.admin_order_field = 'severity'

    def log_type_badge(self, obj):
        return obj.get_log_type_display()
    log_type_badge.short_description = 'Тип'
    log_type_badge.admin_order_field = 'log_type'

    def session_link(self, obj):
        if obj.session:
            url = reverse('admin:assistant_chatsession_change', args=[obj.session.pk])
            return format_html('<a href="{}">{}</a>', url, obj.session.session_id[:8] + '...')
        return '-'
    session_link.short_description = 'Сессия'

    def message_short(self, obj):
        text = obj.message[:80] + '...' if len(obj.message) > 80 else obj.message
        return text
    message_short.short_description = 'Сообщение'

    def response_time(self, obj):
        if obj.response_time_ms:
            return f'{obj.response_time_ms} мс'
        return '-'
    response_time.short_description = 'Время ответа'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
