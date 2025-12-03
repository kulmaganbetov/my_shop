# assistant/services/product_search.py

import requests
from django.conf import settings
import logging

logger = logging.getLogger('assistant')


class ProductSearchService:
    """Сервис для поиска товаров через внешний API"""
    
    API_URL = "https://my-products-api-dusky.vercel.app/api/products"
    
    @classmethod
    def search(cls, query: str = "", category: str = "", limit: int = 200,
               min_credit: float = None, max_credit: float = None) -> list:
        """
        Поиск товаров с расширенными фильтрами.

        Args:
            query: поисковый запрос (ищет по name ИЛИ sku)
            category: категория товаров
            limit: ограничение количества товаров
            min_credit: минимальная цена (credit)
            max_credit: максимальная цена (credit)

        Returns:
            list: список найденных товаров
        """
        try:
            params = {
                "q": query,
                "category": category,
                "limit": limit
            }

            # Добавляем фильтры по цене если указаны
            if min_credit is not None:
                params["min_credit"] = min_credit
            if max_credit is not None:
                params["max_credit"] = max_credit

            logger.info(f"Searching products: query='{query}', category='{category}', "
                       f"price_range=[{min_credit or 'any'}, {max_credit or 'any'}], limit={limit}")

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
    def get_components_for_build(cls, budget: int = None, tier: str = "mid") -> dict:
        """
        Получает все необходимые товары для сборки ПК с умным распределением бюджета.

        Args:
            budget: Общий бюджет на сборку (если указан)
            tier: Уровень сборки ("budget", "mid", "high")

        Returns:
            dict: Словарь {категория: [список товаров]}.
        """
        required_categories = [
            "процессоры", "видеокарты", "материнские платы",
            "корпуса", "блоки питания", "твердотельные диски (ssd)"
        ]

        # Умное распределение бюджета по компонентам (в процентах)
        budget_allocation = {
            "процессоры": 0.25,           # 25% - процессор
            "видеокарты": 0.35,           # 35% - видеокарта (самое важное для игр)
            "материнские платы": 0.15,    # 15% - материнская плата
            "твердотельные диски (ssd)": 0.10,  # 10% - SSD
            "блоки питания": 0.10,        # 10% - блок питания
            "корпуса": 0.05               # 5% - корпус
        }

        build_products = {}

        for category_name in required_categories:
            # Определяем диапазон цен для категории
            # Проверяем, что budget это число, а не строка типа 'pc_budget_ask'
            if budget and isinstance(budget, (int, float)):
                category_budget = budget * budget_allocation.get(category_name, 0.15)
                # Добавляем гибкость ±30%
                min_price = category_budget * 0.5
                max_price = category_budget * 1.5
            else:
                # Если бюджет не указан, используем стандартные диапазоны по tier
                price_ranges = {
                    "budget": (0, 150000),
                    "mid": (100000, 400000),
                    "high": (300000, 2000000)
                }
                min_price, max_price = price_ranges.get(tier.lower(), price_ranges["mid"])
                # Корректируем под категорию
                if category_name == "видеокарты":
                    min_price *= 1.5
                    max_price *= 2
                elif category_name in ["корпуса", "блоки питания"]:
                    max_price *= 0.5

            logger.info(f"Fetching {category_name}: price range {min_price:.0f}-{max_price:.0f}")

            # Получаем товары с умной фильтрацией по цене
            products = cls.search(
                query="",
                category=category_name,
                min_credit=min_price,
                max_credit=max_price,
                limit=30  # Увеличили с 10 до 30 для лучшего выбора
            )

            # Фильтруем по наличию
            in_stock_products = cls.filter_in_stock(products)

            if in_stock_products:
                build_products[category_name] = in_stock_products
                logger.info(f"Found {len(in_stock_products)} in-stock {category_name}")
            else:
                logger.warning(f"No in-stock products found for category: {category_name}")

        return build_products