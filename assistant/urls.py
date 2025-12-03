from django.urls import path
from assistant import views

app_name = 'assistant'

urlpatterns = [
    path('', views.chat_page, name='chat_page'),
    path('api/chat/', views.chat_assistant, name='chat_assistant'),
    path('api/product/<str:sku>/', views.get_product_details, name='product_details'),
]