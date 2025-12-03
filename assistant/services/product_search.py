# assistant/services/product_search.py

import requests
from django.conf import settings
import logging

logger = logging.getLogger('assistant')


class ProductSearchService:
    """Сервис для поиска товаров через внешний API"""
    
    API_URL = "https://my-products-api-dusky.vercel.app/api/products"
    
    @classmethod
    def search(cls, query: str = "", category: str = "", limit: int = 200) -> list:
        """
        Поиск товаров.
        
        Args:
            query: поисковый запрос (ищет по name ИЛИ sku)
            category: категория товаров
            limit: ограничение количества товаров (УВЕЛИЧЕНО до 1000 для категории)
            
        Returns:
            list: список найденных товаров
        """
        try:
            params = {
                "q": query,
                "category": category,
                "limit": limit
            }
            
            logger.info(f"Searching products: query='{query}', category='{category}', limit={limit}")
            
            response = requests.get(cls.API_URL, params=params, timeout=10)
            response.raise_for_status()
            
            products = response.json()
            logger.info(f"Found {len(products)} products")
            
            return products
            
        except requests.RequestException as e:
            logger.error(f"Error searching products: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in product search: {e}")
            return []
    
    @classmethod
    def get_by_sku(cls, sku: str) -> dict:
        """
        Получить товар по SKU. 
        Использует search(query=sku) для точечной выборки через Vercel API.
        """
        try:
            # Оптимизированный поиск: передаем SKU как поисковый запрос (query),
            # Vercel API найдет точное совпадение. Устанавливаем лимит 1, так как SKU уникален.
            products = cls.search(query=sku, limit=1) 
            
            # На всякий случай делаем проверку на стороне клиента.
            product = next((p for p in products if p.get("sku") == sku), None)
            
            if product:
                logger.info(f"Product found: {sku}")
            else:
                logger.warning(f"Product not found by SKU: {sku}")
                
            return product
            
        except Exception as e:
            logger.error(f"Error getting product by SKU: {e}")
            return None
    
    @classmethod
    def filter_by_price(cls, products: list, max_price: float) -> list:
        """
        Фильтр товаров по максимальной цене, используя поле 'credit'.
        (ИСПРАВЛЕНО для использования 'credit')
        """
        return [p for p in products if p.get('credit', 0) <= max_price]
    
    @classmethod
    def filter_in_stock(cls, products: list) -> list:
        """Фильтр товаров в наличии"""
        return [p for p in products if p.get('stock', 0) > 0]


    @classmethod
    def get_components_for_build(cls) -> dict:
        """
        Получает все необходимые товары для сборки ПК, запрашивая каждую категорию отдельно.
        Оптимизировано для больших баз данных (20,000+ товаров).
        
        Returns:
            dict: Словарь {категория: [список товаров]}.
        """
        required_categories = [
            "процессоры", "видеокарты", "материнские платы", 
            "корпуса", "блоки питания", "твердотельные диски (ssd)"
        ]
        
        build_products = {}
        
        for category_name in required_categories:
            # Выполняем ЦЕЛЕВОЙ запрос, используя только фильтр 'category'. 
            # Лимит 1000 достаточен для получения всех товаров в рамках одной категории.
            products = cls.search(query="", category=category_name) 
            
            # Фильтруем по наличию
            in_stock_products = cls.filter_in_stock(products)
            
            if in_stock_products:
                build_products[category_name] = in_stock_products
            else:
                logger.warning(f"No in-stock products found for category: {category_name}")
            
        return build_products