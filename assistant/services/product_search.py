# assistant/services/product_search.py

import requests
from django.conf import settings
import logging
import re
from typing import Optional, List, Dict, Any

logger = logging.getLogger('assistant')


class ProductSearchService:
    """
    Сервис для поиска товаров через внешний API.

    УЛУЧШЕНО:
    - Нормализация поисковых запросов
    - Умные fallback стратегии
    - Детальное логирование
    - Валидация данных
    """

    API_URL = "https://my-products-api-dusky.vercel.app/api/products"

    # Маппинг популярных сокращений и вариаций названий
    QUERY_NORMALIZATIONS = {
        # Процессоры
        r'\bрайзен\b': 'Ryzen',
        r'\bинтел\b': 'Intel',
        r'\bам4\b': 'AM4',
        r'\bам5\b': 'AM5',
        # Видеокарты
        r'\bртх\b': 'RTX',
        r'\bгтх\b': 'GTX',
        r'\bрадеон\b': 'Radeon',
        # Смартфоны
        r'\bайфон\b': 'iPhone',
        r'\bсамсунг\b': 'Samsung',
        r'\bсяоми\b': 'Xiaomi',
        r'\bхуавей\b': 'Huawei',
        # Периферия
        r'\bлоджитек\b': 'Logitech',
        r'\bрейзер\b': 'Razer',
    }

    @classmethod
    def _normalize_query(cls, query: str) -> str:
        """Нормализует поисковый запрос, заменяя кириллические названия на латинские."""
        if not query:
            return query

        normalized = query
        for pattern, replacement in cls.QUERY_NORMALIZATIONS.items():
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

        if normalized != query:
            logger.debug(f"Query normalized: '{query}' -> '{normalized}'")

        return normalized

    @classmethod
    def search(cls, query: str = "", category: str = "", limit: int = 200,
               min_credit: float = None, max_credit: float = None) -> List[Dict[str, Any]]:
        """
        Поиск товаров с расширенными фильтрами.

        Args:
            query: поисковый запрос (ищет по name ИЛИ sku)
            category: категория товаров
            limit: ограничение количества товаров
            min_credit: минимальная цена (credit)
            max_credit: максимальная цена (credit)

        Returns:
            list: список найденных товаров с валидированными данными
        """
        try:
            # Нормализуем запрос
            normalized_query = cls._normalize_query(query)

            params = {
                "q": normalized_query,
                "category": category,
                "limit": limit
            }

            if min_credit is not None:
                params["min_credit"] = min_credit
            if max_credit is not None:
                params["max_credit"] = max_credit

            logger.info(f"[SEARCH] query='{normalized_query}', category='{category}', "
                       f"price=[{min_credit or '-'}, {max_credit or '-'}], limit={limit}")

            response = requests.get(cls.API_URL, params=params, timeout=15)
            response.raise_for_status()

            products = response.json()

            # Валидируем и нормализуем данные товаров
            validated_products = cls._validate_products(products)

            logger.info(f"[SEARCH RESULT] Found {len(validated_products)} valid products")
            return validated_products

        except requests.Timeout:
            logger.error(f"[SEARCH ERROR] API timeout for query='{query}'")
            return []
        except requests.RequestException as e:
            logger.error(f"[SEARCH ERROR] API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"[SEARCH ERROR] Unexpected error: {e}", exc_info=True)
            return []

    @classmethod
    def _validate_products(cls, products: List[Dict]) -> List[Dict]:
        """Валидирует и нормализует данные товаров."""
        validated = []
        for p in products:
            try:
                # Проверяем обязательные поля
                if not p.get('sku') or not p.get('name'):
                    continue

                # Нормализуем числовые поля
                validated.append({
                    'sku': str(p.get('sku')),
                    'name': str(p.get('name', '')),
                    'category': str(p.get('category', '')),
                    'brand': str(p.get('brand', '')),
                    'credit': float(p.get('credit', 0)),
                    'bonus': float(p.get('bonus', 0)),
                    'stock': int(p.get('stock', 0)),
                    'warranty': str(p.get('warranty', '')),
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid product data for SKU {p.get('sku')}: {e}")
                continue

        return validated

    @classmethod
    def search_with_fallback(cls, query: str = "", category: str = "",
                             budget: float = None) -> List[Dict[str, Any]]:
        """
        Умный поиск с несколькими fallback стратегиями.

        Порядок поиска:
        1. Точный поиск (query + category)
        2. Только по категории (если query не дал результатов)
        3. Только по запросу (без категории)
        4. Расширенный поиск по словам из запроса
        """
        logger.info(f"[SMART SEARCH] Starting: query='{query}', category='{category}', budget={budget}")

        # Стратегия 1: Полный поиск
        products = cls.search(query=query, category=category, max_credit=budget)
        if products:
            logger.info(f"[SMART SEARCH] Strategy 1 (full) succeeded: {len(products)} products")
            return products

        # Стратегия 2: Только категория
        if category:
            products = cls.search(query="", category=category, max_credit=budget)
            if products:
                logger.info(f"[SMART SEARCH] Strategy 2 (category only) succeeded: {len(products)} products")
                return products

        # Стратегия 3: Только запрос
        if query:
            products = cls.search(query=query, category="", max_credit=budget)
            if products:
                logger.info(f"[SMART SEARCH] Strategy 3 (query only) succeeded: {len(products)} products")
                return products

        # Стратегия 4: Поиск по отдельным словам
        if query and len(query.split()) > 1:
            words = query.split()
            for word in words:
                if len(word) >= 3:  # Минимум 3 символа
                    products = cls.search(query=word, category=category)
                    if products:
                        logger.info(f"[SMART SEARCH] Strategy 4 (word '{word}') succeeded: {len(products)} products")
                        return products

        logger.warning(f"[SMART SEARCH] All strategies failed for query='{query}', category='{category}'")
        return []
    
    @classmethod
    def get_by_sku(cls, sku: str) -> Optional[Dict[str, Any]]:
        """
        Получить товар по SKU.

        УЛУЧШЕНО: Строгая проверка SKU и расширенный поиск.
        """
        if not sku:
            logger.warning("[GET_BY_SKU] Empty SKU provided")
            return None

        sku_str = str(sku).strip()
        logger.info(f"[GET_BY_SKU] Looking for SKU: {sku_str}")

        try:
            # Поиск по SKU
            products = cls.search(query=sku_str, limit=10)

            # Строгая проверка SKU (точное совпадение)
            for p in products:
                if str(p.get("sku")) == sku_str:
                    logger.info(f"[GET_BY_SKU] Found: {p.get('name')} (credit={p.get('credit')})")
                    return p

            logger.warning(f"[GET_BY_SKU] Not found: {sku_str}")
            return None

        except Exception as e:
            logger.error(f"[GET_BY_SKU] Error: {e}", exc_info=True)
            return None
    
    @classmethod
    def filter_by_price(cls, products: List[Dict], max_price: float) -> List[Dict]:
        """
        Фильтр товаров по максимальной цене (поле credit).

        УЛУЧШЕНО: Добавлена валидация и логирование.
        """
        if not products:
            return []

        if not max_price:
            return products

        try:
            max_price_num = float(max_price)
            filtered = []

            for p in products:
                try:
                    credit = float(p.get('credit', 0))
                    if credit <= max_price_num:
                        filtered.append(p)
                except (ValueError, TypeError):
                    logger.warning(f"[FILTER] Invalid credit for SKU {p.get('sku')}: {p.get('credit')}")
                    continue

            logger.debug(f"[FILTER BY PRICE] {len(products)} -> {len(filtered)} (max={max_price_num})")
            return filtered

        except (ValueError, TypeError) as e:
            logger.error(f"[FILTER] Invalid max_price: {max_price}, error: {e}")
            return products

    @classmethod
    def filter_in_stock(cls, products: List[Dict]) -> List[Dict]:
        """Фильтр товаров в наличии."""
        if not products:
            return []

        filtered = [p for p in products if int(p.get('stock', 0)) > 0]
        logger.debug(f"[FILTER IN STOCK] {len(products)} -> {len(filtered)}")
        return filtered


    @classmethod
    def get_components_for_build(cls, budget: int = None, tier: str = "mid",
                                  include_peripherals: bool = False) -> Dict[str, List[Dict]]:
        """
        Получает все необходимые товары для сборки ПК с умным распределением бюджета.

        УЛУЧШЕНО:
        - Более точное распределение бюджета
        - Fallback стратегии при отсутствии товаров
        - Детальное логирование для отладки

        Args:
            budget: Общий бюджет на сборку (если указан)
            tier: Уровень сборки ("budget", "mid", "high")
            include_peripherals: Включить периферию

        Returns:
            dict: Словарь {категория: [список товаров]}
        """
        logger.info(f"[PC BUILD] Starting component search: budget={budget}, tier={tier}, peripherals={include_peripherals}")

        # Базовые компоненты
        required_categories = [
            "процессоры", "видеокарты", "материнские платы",
            "корпуса", "блоки питания", "твердотельные диски (ssd)"
        ]

        if include_peripherals:
            required_categories.extend(["мониторы", "мыши", "клавиатуры"])
            logger.info("[PC BUILD] Adding peripherals to build")

        # Распределение бюджета
        if include_peripherals:
            budget_allocation = {
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
        else:
            budget_allocation = {
                "процессоры": 0.25,
                "видеокарты": 0.35,
                "материнские платы": 0.15,
                "твердотельные диски (ssd)": 0.10,
                "блоки питания": 0.10,
                "корпуса": 0.05
            }

        # Ценовые диапазоны по tier (без бюджета)
        tier_ranges = {
            "budget": {"base": (5000, 80000), "gpu": (30000, 150000)},
            "mid": {"base": (20000, 200000), "gpu": (100000, 400000)},
            "high": {"base": (50000, 500000), "gpu": (300000, 1500000)}
        }

        build_products = {}

        for category_name in required_categories:
            logger.info(f"[PC BUILD] Searching for: {category_name}")

            # Определяем ценовой диапазон
            if budget and isinstance(budget, (int, float)) and budget > 0:
                allocation = budget_allocation.get(category_name, 0.10)
                category_budget = budget * allocation
                min_price = category_budget * 0.4  # -60%
                max_price = category_budget * 2.0  # +100%
            else:
                # Используем tier-based диапазоны
                tier_config = tier_ranges.get(tier.lower(), tier_ranges["mid"])

                if category_name == "видеокарты":
                    min_price, max_price = tier_config["gpu"]
                elif category_name in ["корпуса", "блоки питания"]:
                    min_price, max_price = 5000, 150000
                elif category_name in ["мыши", "клавиатуры"]:
                    min_price, max_price = 3000, 80000
                elif category_name == "мониторы":
                    min_price, max_price = 40000, 400000
                else:
                    min_price, max_price = tier_config["base"]

            logger.debug(f"[PC BUILD] {category_name}: price range {min_price:.0f}-{max_price:.0f}")

            # Стратегия 1: Поиск в ценовом диапазоне
            products = cls.search(
                query="",
                category=category_name,
                min_credit=min_price,
                max_credit=max_price,
                limit=40
            )
            in_stock = cls.filter_in_stock(products)

            # Стратегия 2: Расширенный диапазон
            if not in_stock:
                logger.warning(f"[PC BUILD] No products in price range for {category_name}, trying extended range")
                products = cls.search(
                    query="",
                    category=category_name,
                    min_credit=min_price * 0.3,
                    max_credit=max_price * 2.5,
                    limit=50
                )
                in_stock = cls.filter_in_stock(products)

            # Стратегия 3: Без ценового фильтра
            if not in_stock:
                logger.warning(f"[PC BUILD] Extended range failed for {category_name}, trying without price filter")
                products = cls.search(
                    query="",
                    category=category_name,
                    limit=50
                )
                in_stock = cls.filter_in_stock(products)

            if in_stock:
                build_products[category_name] = in_stock
                logger.info(f"[PC BUILD] ✓ {category_name}: {len(in_stock)} products found")
            else:
                logger.error(f"[PC BUILD] ✗ {category_name}: NO PRODUCTS AVAILABLE")

        # Итоговый отчет
        found = len(build_products)
        required = len(required_categories)
        logger.info(f"[PC BUILD] Complete: {found}/{required} categories found")

        return build_products