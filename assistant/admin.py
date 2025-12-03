from django.contrib import admin
from assistant.models import ChatSession, ChatMessage

@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['session_id', 'user', 'created_at']
    list_filter = ['created_at']
    search_fields = ['session_id', 'user__username']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['session', 'is_user', 'intent', 'timestamp']
    list_filter = ['is_user', 'intent', 'timestamp']
    search_fields = ['message', 'session__session_id']
