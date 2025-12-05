from django.urls import path
from assistant import views

app_name = 'assistant'

urlpatterns = [
    path('', views.chat_page, name='chat_page'),
    path('api/chat/', views.chat_assistant, name='chat_assistant'),
    path('api/product/<str:sku>/', views.get_product_details, name='product_details'),
    path('api/history/<str:session_id>/', views.get_chat_history, name='chat_history'),
    path('api/request-manager/', views.request_manager, name='request_manager'),
    path('api/messages/<str:session_id>/', views.get_new_messages, name='new_messages'),
    path('api/status/<str:session_id>/', views.get_session_status, name='session_status'),
]
