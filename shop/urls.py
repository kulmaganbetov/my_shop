from django.urls import path
from shop.views import main_views

app_name = 'shop'

urlpatterns = [
    path('', main_views.index, name='index'),
    path('products/', main_views.product_list, name='product_list'),
    path('products/<str:sku>/', main_views.product_detail, name='product_detail'),
]