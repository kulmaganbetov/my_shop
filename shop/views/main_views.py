from django.shortcuts import render
from django.http import JsonResponse

def index(request):
    '''Главная страница'''
    return render(request, 'shop/index.html')

def product_list(request):
    '''Список товаров'''
    return render(request, 'shop/product_list.html')

def product_detail(request, sku):
    '''Детали товара'''
    context = {'sku': sku}
    return render(request, 'shop/product_detail.html', context)