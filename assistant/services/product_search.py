# assistant/services/product_search.py
"""
Сервис для поиска товаров через внешний API

Поддерживает новый формат API с пагинацией и метаданными.
"""

import requests
from dataclasses import dataclass
from typing import Optional, Tuple
import logging

logger = logging.getLogger('assistant')


@dataclass
class SearchResult:
    """Результат поиска с метаданными"""
    products: list
    total: int
    has_more: bool
    next_offset: Optional[int]


# Конфигурация распределения бюджета
BUDGET_ALLOCATION_WITH_PERIPHERALS = {
    "процессоры": 0.18,
    "видеокарты": 0.25,
    "материнские платы": 0.10,
    "твердотельные диски (ssd)": 0.07,
    "блоки питания": 0.07,
    "корпуса": 0.03,
    "мониторы": 0.20,
    "мыши": 0.05,
    "клавиатуры": 0.05
}

BUDGET_ALLOCATION_PC_ONLY = {
    "процессоры": 0.25,
    "видеокарты": 0.35,
    "материнские платы": 0.15,
    "твердотельные диски (ssd)": 0.10,
    "блоки питания": 0.10,
    "корпуса": 0.05
}

PC_REQUIRED_CATEGORIES = [
    "процессоры", "видеокарты", "материнские платы",
    "корпуса", "блоки питания", "твердотельные диски (ssd)"
]

PERIPHERAL_CATEGORIES = ["мониторы", "мыши", "клавиатуры"]


class ProductSearchService:
    """Сервис для поиска товаров через внешний API"""

    API_URL = "https://my-products-api-dusky.vercel.app/api/products"

    @classmethod
    def search(cls, query: str = "", category: str = "", limit: int = 50,
               offset: int = 0, min_credit: float = None, max_credit: float = None,
               in_stock: bool = False, sort: str = "credit", order: str = "asc") -> list:
        """
        Поиск товаров с расширенными фильтрами.

        Args:
            query: поисковый запрос (ищет по name ИЛИ sku)
            category: категория товаров
            limit: ограничение количества товаров (max 200)
            offset: смещение для пагинации
            min_credit: минимальная цена
            max_credit: максимальная цена
            in_stock: только товары в наличии
            sort: поле сортировки (credit, name, stock)
            order: направление сортировки (asc, desc)

        Returns:
            list: список найденных товаров
        """
        result = cls.search_with_metadata(
            query=query, category=category, limit=limit, offset=offset,
            min_credit=min_credit, max_credit=max_credit,
            in_stock=in_stock, sort=sort, order=order
        )
        return result.products

    @classmethod
    def search_with_metadata(cls, query: str = "", category: str = "", limit: int = 50,
                              offset: int = 0, min_credit: float = None, max_credit: float = None,
                              in_stock: bool = False, sort: str = "credit",
                              order: str = "asc") -> SearchResult:
        """
        Поиск товаров с возвратом метаданных (total, has_more).

        Returns:
            SearchResult: объект с products, total, has_more, next_offset
        """
        try:
            params = {
                "q": query,
                "category": category,
                "limit": min(limit, 200),
                "offset": offset,
                "sort": sort,
                "order": order
            }

            if min_credit is not None:
                params["min_credit"] = min_credit
            if max_credit is not None:
                params["max_credit"] = max_credit
            if in_stock:
                params["in_stock"] = "true"

            logger.info(f"Searching products: query='{query}', category='{category}', "
                        f"price_range=[{min_credit or 'any'}, {max_credit or 'any'}], "
                        f"limit={limit}, offset={offset}")

            response = requests.get(cls.API_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Поддержка нового и старого формата API
            if isinstance(data, dict) and "data" in data:
                # Новый формат с метаданными
                products = data.get("data", [])
                meta = data.get("meta", {})
                total = meta.get("total", len(products))
                has_more = meta.get("has_more", False)
                next_offset = meta.get("next_offset")
            else:
                # Старый формат - просто массив
                products = data if isinstance(data, list) else []
                total = len(products)
                has_more = len(products) >= limit
                next_offset = offset + limit if has_more else None

            logger.info(f"Found {len(products)} products (total: {total}, has_more: {has_more})")

            return SearchResult(
                products=products,
                total=total,
                has_more=has_more,
                next_offset=next_offset
            )

        except requests.RequestException as e:
            logger.error(f"Error searching products: {e}")
            return SearchResult(products=[], total=0, has_more=False, next_offset=None)
        except Exception as e:
            logger.error(f"Unexpected error in product search: {e}")
            return SearchResult(products=[], total=0, has_more=False, next_offset=None)

    @classmethod
    def get_by_sku(cls, sku: str) -> Optional[dict]:
        """Получить товар по SKU."""
        try:
            result = cls.search_with_metadata(query=sku, limit=10)

            # Точное совпадение SKU
            product = next((p for p in result.products if p.get("sku") == sku), None)

            if product:
                logger.info(f"Product found: {sku}")
            else:
                logger.warning(f"Product not found by SKU: {sku}")

            return product

        except Exception as e:
            logger.error(f"Error getting product by SKU: {e}")
            return None

    @classmethod
    def filter_in_stock(cls, products: list) -> list:
        """Фильтр товаров в наличии"""
        return [p for p in products if int(p.get('stock', 0)) > 0]

    @classmethod
    def filter_by_price(cls, products: list, min_price: float = None,
                        max_price: float = None) -> list:
        """Фильтр товаров по диапазону цен"""
        filtered = []
        min_p = float(min_price) if min_price else 0
        max_p = float(max_price) if max_price else float('inf')

        for p in products:
            try:
                credit = float(p.get('credit', 0))
                if min_p <= credit <= max_p:
                    filtered.append(p)
            except (ValueError, TypeError):
                continue

        return filtered

    @classmethod
    def get_budget_allocation(cls, include_peripherals: bool = False) -> dict:
        """Получить распределение бюджета по категориям"""
        if include_peripherals:
            return BUDGET_ALLOCATION_WITH_PERIPHERALS.copy()
        return BUDGET_ALLOCATION_PC_ONLY.copy()

    @classmethod
    def get_required_categories(cls, include_peripherals: bool = False) -> list:
        """Получить список необходимых категорий для сборки"""
        categories = PC_REQUIRED_CATEGORIES.copy()
        if include_peripherals:
            categories.extend(PERIPHERAL_CATEGORIES)
        return categories

    @classmethod
    def calculate_category_budget(cls, total_budget: int, category: str,
                                   include_peripherals: bool = False) -> Tuple[float, float, float]:
        """
        Рассчитать целевую цену и диапазон для категории.

        Returns:
            Tuple[target, min_price, max_price]
        """
        allocation = cls.get_budget_allocation(include_peripherals)
        percentage = allocation.get(category, 0.10)
        target = total_budget * percentage
        # ±20% от целевой цены
        min_price = target * 0.7
        max_price = target * 1.2
        return target, min_price, max_price

    @classmethod
    def get_components_for_build(cls, budget: int = None, tier: str = "mid",
                                  include_peripherals: bool = False) -> Tuple[dict, dict]:
        """
        Получает все необходимые товары для сборки ПК с умным распределением бюджета.

        Args:
            budget: Общий бюджет на сборку (если указан)
            tier: Уровень сборки ("budget", "mid", "high")
            include_peripherals: Включить периферию (мышь, клавиатуру, монитор)

        Returns:
            Tuple[dict, dict]: (словарь продуктов по категориям, целевые цены)
        """
        required_categories = cls.get_required_categories(include_peripherals)

        if include_peripherals:
            logger.info("Peripherals requested - adding monitor, mouse, keyboard")

        build_products = {}
        category_targets = {}

        # Диапазоны по умолчанию для tier
        tier_ranges = {
            "budget": (0, 150000),
            "mid": (100000, 400000),
            "high": (300000, 2000000)
        }

        for category_name in required_categories:
            if budget and isinstance(budget, (int, float)):
                target, min_price, max_price = cls.calculate_category_budget(
                    budget, category_name, include_peripherals
                )
                category_targets[category_name] = target
            else:
                # Диапазоны по умолчанию
                if category_name in ["корпуса", "блоки питания"]:
                    min_price, max_price = 0, 200000
                elif category_name in ["мыши", "клавиатуры"]:
                    min_price, max_price = 0, 100000
                elif category_name == "мониторы":
                    min_price, max_price = 50000, 500000
                else:
                    min_price, max_price = tier_ranges.get(tier.lower(), tier_ranges["mid"])
                    if category_name == "видеокарты":
                        min_price *= 1.5
                        max_price *= 2

            logger.info(f"Fetching {category_name}: price range {min_price:.0f}-{max_price:.0f}")

            # Получаем товары с фильтрацией по цене и наличию
            products = cls.search(
                query="",
                category=category_name,
                min_credit=min_price,
                max_credit=max_price,
                in_stock=True,
                limit=30,
                sort="credit",
                order="asc"
            )

            if products:
                build_products[category_name] = products
                logger.info(f"Found {len(products)} in-stock {category_name}")
            else:
                logger.warning(f"No in-stock products found for category: {category_name}")

        return build_products, category_targets
