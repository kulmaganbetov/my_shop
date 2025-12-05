# ===============================
# assistant/services/gpt_service.py
# ===============================
import json
import os
import base64
from openai import OpenAI
from django.conf import settings
import logging
import re

logger = logging.getLogger('assistant')

# Initialize OpenAI client with new API
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY', ''))

# Вспомогательная функция для формирования массива сообщений
def _build_messages(system_prompt: str, context: list) -> list:
    """Создает полный массив сообщений для OpenAI API, включая системный промпт и контекст."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(context) 
    return messages


class GPTService:
    """Сервис для работы с OpenAI GPT API"""
    
    @staticmethod
    def analyze_query(context: list) -> dict:
        """Анализ запроса пользователя"""
        system_prompt = """Ты - аналитик запросов для интернет-магазина электроники.
Твоя задача - понять намерение пользователя и извлечь параметры поиска. Если клиент писал имя товара по другому ты сформулируй имя товара и верни. Например если человек писал айфон ты пиши iPhone, если ртх 3050, RTX 3050. Правильно понимай запрос пользователя, например если клиент спросил "ищу процессор для видеокарты asus rog" значит клиент ищет процессор.

Доступные категории: смартфоны, процессоры, видеокарты, мониторы, корпуса, карты памяти, блоки питания, канцтовары, ноутбуки, мыши, веб-камеры, Внешние HDD/SSD, кабели, машрутизаторы, Коврики для мыши, Коммутаторы, Клавиатуры, Твердотельные диски (SSD)

Определи:
1. intent: "product_search" (поиск товара), "faq" (вопрос о магазине/заказе), "general" (общение), "pc_build" (финальная сборка), "pc_budget_ask" (запрос бюджета для сборки)
2. category: категория товара (если intent=product_search)
3. search_query: ключевые слова для поиска (бренд, модель, характеристики)
4. budget: бюджет пользователя в тенге (если указан, например "до 50000"). Если intent=pc_build, а бюджет не указан, верни "pc_budget_ask".
5. requirements: особые требования (игры, работа, учеба и т.д.)
6. build_tier: Ценовой сегмент для сборки ("budget", "mid", "high"). (Только если бюджет не указан)
7. is_detailed_query: true если клиент просит аналоги/рекомендации/сравнение/что лучше, false если ищет конкретную модель
8. include_peripherals: true если клиент ЯВНО просит добавить периферию (мышь, клавиатуру, монитор) к сборке ПК. Ключевые фразы: "с монитором", "с мышкой", "с клавиатурой", "полный комплект", "рабочее место", "все для работы/игр". По умолчанию false - только системный блок.

Ответь ТОЛЬКО в формате JSON без дополнительного текста.

Пример 1 (Запрос бюджета):
{
  "intent": "pc_budget_ask",
  "requirements": "для работы",
  "build_tier": "mid",
  "include_peripherals": false
}

Пример 2 (Финальная сборка с периферией):
{
  "intent": "pc_build",
  "requirements": "для игр с монитором и мышкой",
  "budget": 700000,
  "include_peripherals": true
}

Пример 3 (Точный запрос - конкретная модель):
{
  "intent": "product_search",
  "category": "процессоры",
  "search_query": "AMD Ryzen 5 5600X",
  "budget": null,
  "requirements": "",
  "is_detailed_query": false
}

Пример 4 (Сборка без периферии - только системный блок):
{
  "intent": "pc_build",
  "requirements": "для игр",
  "budget": 500000,
  "include_peripherals": false
}

Ответь ТОЛЬКО в формате JSON без дополнительного текста."""
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=_build_messages(system_prompt, context),
                temperature=0.3,
                max_tokens=300
            )

            result = json.loads(response.choices[0].message.content)
            logger.info(f"Query analysis: {result}")
            return result

        except Exception as e:
            logger.error(f"Error analyzing query: {e}")
            return {
                "intent": "general",
                "category": "",
                "search_query": "",
                "budget": None,
                "requirements": ""
            }
    
    @staticmethod
    def select_pc_components(all_products_by_category: dict, user_requirements: str,
                           budget_tier: str, max_budget: int = None,
                           include_peripherals: bool = False) -> dict:
        """
        Выбирает оптимальные компоненты для сборки ПК с улучшенной логикой.

        Улучшения:
        - Увеличено количество товаров для анализа (20 вместо 10)
        - Умная сортировка и фильтрация
        - Проверка совместимости компонентов
        - Балансировка CPU/GPU
        - Поддержка периферии (мониторы, мыши, клавиатуры)
        """

        try:
            # Подготовка компактного списка товаров для GPT
            LIMITED_PRODUCTS = {}
            LIMIT_PER_CATEGORY = 20  # Увеличили с 10 до 20

            # Определяем стратегию сортировки
            sort_reverse = (budget_tier.lower() == 'high') or (max_budget and max_budget > 500000)

            for category, products in all_products_by_category.items():
                if not products:
                    continue

                # Сортируем по цене
                sorted_products = sorted(
                    products,
                    key=lambda p: float(p.get('credit', 0)),
                    reverse=sort_reverse
                )

                # Создаем компактное представление с дополнительной информацией
                compact_products = []
                for p in sorted_products[:LIMIT_PER_CATEGORY]:
                    product_info = {
                        "sku": p.get('sku'),
                        "name": p.get('name'),
                        "credit": float(p.get('credit', 0)),
                        "brand": p.get('brand', ''),
                        "stock": p.get('stock', 0)
                    }

                    # Извлекаем дополнительную информацию из названия
                    name_lower = p.get('name', '').lower()

                    # Для процессоров - извлекаем socket
                    if category == "процессоры":
                        if 'am4' in name_lower:
                            product_info['socket'] = 'AM4'
                        elif 'am5' in name_lower:
                            product_info['socket'] = 'AM5'
                        elif 'lga1700' in name_lower or '1700' in name_lower:
                            product_info['socket'] = 'LGA1700'
                        elif 'lga1200' in name_lower or '1200' in name_lower:
                            product_info['socket'] = 'LGA1200'

                    # Для материнских плат - извлекаем socket
                    elif category == "материнские платы":
                        if 'am4' in name_lower:
                            product_info['socket'] = 'AM4'
                        elif 'am5' in name_lower:
                            product_info['socket'] = 'AM5'
                        elif 'lga1700' in name_lower or '1700' in name_lower:
                            product_info['socket'] = 'LGA1700'
                        elif 'lga1200' in name_lower or '1200' in name_lower:
                            product_info['socket'] = 'LGA1200'

                    # Для видеокарт - извлекаем примерную мощность
                    elif category == "видеокарты":
                        # Примерная оценка на основе модели
                        if any(model in name_lower for model in ['rtx 4090', '4090']):
                            product_info['power_req'] = 450
                        elif any(model in name_lower for model in ['rtx 4080', '4080', 'rtx 3090']):
                            product_info['power_req'] = 350
                        elif any(model in name_lower for model in ['rtx 4070', '4070', 'rtx 3080']):
                            product_info['power_req'] = 300
                        elif any(model in name_lower for model in ['rtx 4060', '4060', 'rtx 3070']):
                            product_info['power_req'] = 220
                        elif any(model in name_lower for model in ['rtx 3060', 'rx 6600']):
                            product_info['power_req'] = 170
                        else:
                            product_info['power_req'] = 150

                    # Для блоков питания - извлекаем мощность
                    elif category == "блоки питания":
                        wattage_match = re.search(r'(\d{3,4})\s*w', name_lower)
                        if wattage_match:
                            product_info['wattage'] = int(wattage_match.group(1))

                    compact_products.append(product_info)

                LIMITED_PRODUCTS[category] = compact_products

            products_str = json.dumps(LIMITED_PRODUCTS, ensure_ascii=False, indent=2)

            # Формируем budget_info
            if max_budget:
                budget_info = f"Максимальный бюджет: {max_budget:,} ₸. ВАЖНО: Общая стоимость НЕ ДОЛЖНА превышать этот бюджет!"
            else:
                budget_info = "Бюджет не указан. Выбери оптимальное соотношение цена/качество."

            # Определяем количество компонентов
            component_count = 9 if include_peripherals else 6
            peripherals_note = """
6. **ПЕРИФЕРИЯ** (если запрошена):
   - Монитор: выбирай с учетом видеокарты (для игр - 144Hz+, для работы - IPS)
   - Мышь и клавиатура: предпочитай известные бренды (Logitech, Razer, HyperX)
""" if include_peripherals else ""

            # Формат ответа с периферией или без
            if include_peripherals:
                json_format = """{
  "процессоры": "12345",
  "видеокарты": "67890",
  "материнские платы": "11111",
  "корпуса": "22222",
  "блоки питания": "33333",
  "твердотельные диски (ssd)": "44444",
  "мониторы": "55555",
  "мыши": "66666",
  "клавиатуры": "77777"
}"""
            else:
                json_format = """{
  "процессоры": "12345",
  "видеокарты": "67890",
  "материнские платы": "11111",
  "корпуса": "22222",
  "блоки питания": "33333",
  "твердотельные диски (ssd)": "44444"
}"""

            # Улучшенный system prompt с детальными инструкциями
            system_prompt = f"""Ты — эксперт по сборке ПК. Подбери оптимальную сборку из предоставленных компонентов.

{budget_info}
Сегмент: {budget_tier}
Требования: {user_requirements}
Периферия: {"ДА (монитор, мышь, клавиатура)" if include_peripherals else "НЕТ (только системный блок)"}

КРИТЕРИИ ВЫБОРА:

1. **БЮДЖЕТ** (КРИТИЧНО):
   - Общая стоимость = сумма всех {component_count} компонентов
   - Если бюджет указан: НЕ превышай его!
   - Используй максимум бюджета (±5%)

2. **СОВМЕСТИМОСТЬ** (ОБЯЗАТЕЛЬНО):
   - CPU и Материнская плата: socket должны совпадать (AM4, AM5, LGA1700, LGA1200)
   - Видеокарта и БП: мощность БП >= power_req видеокарты + 150W запас
   - Пример: если GPU требует 300W, нужен БП минимум 450W

3. **БАЛАНС КОМПОНЕНТОВ**:
   - CPU и GPU должны быть сопоставимы по цене (соотношение 1:1.2-1.5)
   - Не ставь дорогую GPU с дешевым CPU (bottleneck!)
   - Материнская плата ~ 15-20% от CPU+GPU

4. **ПРИОРИТЕТЫ**:
   - Для игр: приоритет на GPU (35-40% бюджета)
   - Для работы: баланс CPU/GPU (25-30% каждый)
   - SSD: минимум 512GB, приоритет на известные бренды
   - БП: запас мощности 20-30%, 80+ Bronze или выше

5. **КАЧЕСТВО**:
   - Предпочитай известные бренды
   - stock > 0 обязательно
{peripherals_note}
ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО JSON с SKU (без объяснений):
{json_format}

ВАЖНО: Используй ТОЛЬКО SKU из предоставленного списка!"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Товары:\n\n{products_str}\n\nСобери оптимальный ПК."}
            ]

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.3,
                max_tokens=600
            )

            result_text = response.choices[0].message.content.strip()

            # Очищаем от markdown если есть
            if '```' in result_text:
                result_text = re.sub(r'```json\s*|\s*```', '', result_text).strip()

            result = json.loads(result_text)

            # Валидация результата
            required_categories = ["процессоры", "видеокарты", "материнские платы",
                                 "корпуса", "блоки питания", "твердотельные диски (ssd)"]

            # Добавляем периферию если запрошена
            if include_peripherals:
                required_categories.extend(["мониторы", "мыши", "клавиатуры"])

            if not all(cat in result for cat in required_categories):
                logger.error(f"GPT returned incomplete build: {result}")
                return {}

            logger.info(f"PC build selection successful: {result}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in PC component selection: {e}")
            logger.error(f"GPT response: {result_text if 'result_text' in locals() else 'N/A'}")
            return {}
        except Exception as e:
            logger.error(f"Error selecting PC components: {e}", exc_info=True)
            return {}
            
    @staticmethod
    def generate_pc_build_response(context: list, selected_build_details: dict) -> str:
        """Генерирует ответ с деталями предложенной сборки ПК."""
        try:
            # Используем безопасное извлечение цены
            total_price = sum(float(item.get('credit', 0)) for item in selected_build_details.values() if item.get('credit') is not None)

            build_info = "\n".join([
                # Используем форматирование для разделения тысяч и safe .get()
                f"* **{category.title()}**: {details['name']} ({float(details.get('credit', 0)):,} ₸)"
                for category, details in selected_build_details.items()
            ])

            system_prompt = """Ты — дружелюбный AI-консультант "Роберт". Ты только что собрал идеальный ПК для клиента.
            Твой ответ должен:
            1. Подтвердить готовность сборки и сегмент.
            2. Представить финальную стоимость.
            3. Представить список выбранных компонентов.
            4. Дать краткое обоснование (для игр/работы) и похвалить сборку.
            5. Предложить добавить сборку в корзину или изменить компонент.

            Используй эмодзи (🖥️, ✨, 💰) и Markdown."""

            messages = [{"role": "system", "content": system_prompt}]
            # Ограничиваем историю, чтобы не перегружать промпт
            messages.extend(context[-2:])

            messages.append({
                "role": "user",
                "content": f"""Клиент: {context[-1]['content']}

Детали сборки:
Общая стоимость: {total_price:,} ₸
Компоненты:
{build_info}

Сгенерируй финальный ответ."""
            })

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=800
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error generating PC build response: {e}")
            return "Извините, произошла ошибка при формировании ответа по сборке ПК."



    @staticmethod
    def select_best_products(products: list, user_query: str, requirements: dict) -> list:
        """
        Выбор наиболее подходящих товаров.

        УЛУЧШЕНО: Строгая валидация SKU и возврат точных данных из API.
        """
        if not products:
            logger.warning("select_best_products called with empty product list")
            return []

        try:
            # Ограничиваем количество товаров для анализа
            products_to_analyze = products[:20]

            # Создаем компактное представление для GPT (только ключевые поля)
            compact_products = []
            for p in products_to_analyze:
                compact_products.append({
                    "sku": p.get("sku"),
                    "name": p.get("name"),
                    "credit": float(p.get("credit", 0)),
                    "brand": p.get("brand", ""),
                    "stock": int(p.get("stock", 0))
                })

            budget = requirements.get("budget")
            budget_instruction = f"Бюджет клиента: {budget} ₸. НЕ выбирай товары дороже этой суммы!" if budget else "Бюджет не указан."

            logger.debug(f"Selecting best products from {len(compact_products)} items, budget={budget}")

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"""Ты - эксперт по подбору электроники.
Проанализируй товары и выбери 3-5 наиболее подходящих для запроса пользователя.

{budget_instruction}

КРИТЕРИИ ОТБОРА (по приоритету):
1. Соответствие запросу пользователя (название, бренд, модель)
2. Наличие на складе (stock > 0) - ОБЯЗАТЕЛЬНО
3. Соответствие бюджету (поле credit - это цена в тенге)
4. Качество бренда (Intel, AMD, Samsung, Logitech и т.д.)

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО JSON массив с SKU в порядке релевантности:
["sku1", "sku2", "sku3"]

ВАЖНО: Используй ТОЛЬКО SKU из предоставленного списка!"""
                    },
                    {
                        "role": "user",
                        "content": f"""Запрос клиента: {user_query}
Требования: {requirements.get('requirements', '')}

Доступные товары:
{json.dumps(compact_products, ensure_ascii=False, indent=2)}"""
                    }
                ],
                temperature=0.3,  # Снижен для более предсказуемого выбора
                max_tokens=200
            )
            
            raw_content = response.choices[0].message.content.strip()
            selected_skus = []

            # Очищаем от markdown если есть
            if '```' in raw_content:
                raw_content = re.sub(r'```json\s*|\s*```', '', raw_content).strip()

            try:
                parsed_json = json.loads(raw_content)
                if isinstance(parsed_json, list):
                    # Конвертируем все в строки для сравнения
                    selected_skus = [str(sku) for sku in parsed_json]
                    logger.debug(f"GPT selected SKUs: {selected_skus}")
            except json.JSONDecodeError:
                logger.warning(f"GPT returned invalid JSON for selection: {raw_content[:100]}...")

            # Создаем индекс продуктов по SKU для быстрого поиска
            products_by_sku = {str(p.get("sku")): p for p in products}

            # Выбираем продукты по SKU в порядке, указанном GPT
            selected_products = []
            for sku in selected_skus:
                if sku in products_by_sku:
                    selected_products.append(products_by_sku[sku])
                else:
                    logger.warning(f"GPT returned unknown SKU: {sku}")

            # Fallback: если GPT не выбрал или выбрал невалидные SKU
            if not selected_products:
                logger.warning("No valid products selected by GPT, using first 5 from list")
                selected_products = products[:5]

            logger.info(f"Selected {len(selected_products)} products: {[p.get('sku') for p in selected_products]}")
            return selected_products

        except Exception as e:
            logger.error(f"Error selecting products: {e}", exc_info=True)
            return products[:5]
    
    @staticmethod
    def generate_product_response(context: list, products: list, is_detailed_query: bool = False) -> str:
        """
        Генерация ответа с рекомендацией товаров.

        УЛУЧШЕНО: Строгое использование цен из API данных.

        Args:
            context: История сообщений
            products: Список товаров
            is_detailed_query: True если клиент просит аналоги/рекомендации
        """
        if not products:
            logger.warning("generate_product_response called with empty product list")
            return "К сожалению, не удалось найти подходящие товары."

        try:
            # Формируем СТРОГУЮ информацию о товарах с точными ценами
            products_info_list = []
            for idx, p in enumerate(products[:5], 1):
                sku = p.get('sku', 'N/A')
                name = p.get('name', 'Без названия')
                credit = float(p.get('credit', 0))
                bonus = float(p.get('bonus', 0))
                warranty = p.get('warranty', 'уточняйте')
                stock = int(p.get('stock', 0))
                brand = p.get('brand', '')

                product_block = f"""ТОВАР #{idx} (SKU: {sku})
Название: {name}
Бренд: {brand}
ЦЕНА РАССРОЧКА: {credit:,.0f} ₸
ЦЕНА СО СКИДКОЙ: {bonus:,.0f} ₸
Гарантия: {warranty}
В наличии: {stock} шт."""

                products_info_list.append(product_block)
                logger.debug(f"Product {idx}: SKU={sku}, credit={credit}, bonus={bonus}")

            products_info = "\n\n".join(products_info_list)
            user_message = context[-1]['content'] if context else ""

            # КРИТИЧЕСКИ ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ О ЦЕНАХ
            price_warning = """
⚠️ КРИТИЧЕСКИ ВАЖНО О ЦЕНАХ:
- Используй ТОЛЬКО цены, указанные выше (ЦЕНА РАССРОЧКА и ЦЕНА СО СКИДКОЙ)
- НИКОГДА не придумывай и не округляй цены!
- Копируй цены ТОЧНО как указано в данных товара
- Если цена 234567 ₸ - пиши именно 234,567 ₸, а не 235000 ₸
"""

            if is_detailed_query:
                system_prompt = f"""Ты - эксперт-консультант интернет-магазина электроники OverClockers.
Клиент просит аналоги или рекомендации.

{price_warning}

ФОРМАТ ОТВЕТА:
1. Краткое подтверждение запроса (1 предложение)
2. Для каждого товара (2-3 шт):
   - Название (жирным)
   - 💳 Рассрочка: [ТОЧНАЯ цена из данных] ₸
   - 💰 Скидка: [ТОЧНАЯ цена из данных] ₸
   - 🛡️ Гарантия: [из данных]
   - Краткие преимущества (1-2 пункта)
3. Рекомендация какой выбрать

Используй эмодзи умеренно. Будь дружелюбным."""
            else:
                system_prompt = f"""Ты - консультант интернет-магазина OverClockers.
Клиент спрашивает конкретный товар. Дай КОРОТКИЙ ответ.

{price_warning}

ФОРМАТ ОТВЕТА (строго):
**[Название товара]**
💳 Рассрочка: [ТОЧНАЯ цена] ₸
💰 Скидка: [ТОЧНАЯ цена] ₸
🛡️ Гарантия: [период]

Если товаров 2-3, покажи каждый в таком формате.
В конце - одно предложение с рекомендацией (если уместно).

НЕ пиши длинных описаний! НЕ выдумывай цены!"""

            messages = [{"role": "system", "content": system_prompt}]

            # Ограничиваем контекст для экономии токенов
            if len(context) > 3:
                messages.extend(context[-3:-1])
            elif len(context) > 1:
                messages.extend(context[:-1])

            messages.append({
                "role": "user",
                "content": f"""Запрос клиента: {user_message}

ДАННЫЕ ТОВАРОВ (используй ТОЛЬКО эти цены!):
{products_info}

{"Помоги выбрать с пояснениями." if is_detailed_query else "Ответь кратко с точными ценами."}"""
            })

            max_tokens = 700 if is_detailed_query else 400

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.5,  # Снижен для более точных ответов
                max_tokens=max_tokens
            )

            result = response.choices[0].message.content
            logger.info(f"Generated product response for {len(products)} products, detailed={is_detailed_query}")
            return result

        except Exception as e:
            logger.error(f"Error generating product response: {e}", exc_info=True)
            # Fallback: формируем простой ответ без GPT
            fallback_response = "Найденные товары:\n\n"
            for p in products[:3]:
                fallback_response += f"**{p.get('name', 'Товар')}**\n"
                fallback_response += f"💳 Рассрочка: {float(p.get('credit', 0)):,.0f} ₸\n"
                fallback_response += f"💰 Скидка: {float(p.get('bonus', 0)):,.0f} ₸\n\n"
            return fallback_response
    
    @staticmethod
    def generate_faq_response(context: list, faq_context: str) -> str:
        """Генерация ответа на FAQ"""
        try:
            system_prompt = f"""Ты - дружелюбный консультант интернет-магазина электроники.
Отвечай на вопросы клиентов о доставке, оплате, возврате и других услугах магазина.

Информация о магазине:
{faq_context}

Правила:
- Будь вежливым и информативным
- Отвечай кратко, но полно
- Используй эмодзи для визуальности
- Если информации нет в базе, предложи связаться с поддержкой"""
            
            messages = _build_messages(system_prompt, context)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error generating FAQ response: {e}")
            return "Извините, произошла ошибка. Свяжитесь с нашей поддержкой."
    
    @staticmethod
    def generate_general_response(context: list) -> str:
        """Генерация общего ответа"""
        try:
            system_prompt = """Ты - Роберт, дружелюбный ассистент интернет-магазина электроники Over.
Помогай клиентам, отвечай на вопросы, направляй их к нужным товарам или услугам.
Будь вежливым, профессиональным и полезным."""
            
            messages = _build_messages(system_prompt, context)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.8,
                max_tokens=300
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error generating general response: {e}")
            return "Привет! Чем могу помочь?"


    @staticmethod
    def generate_budget_request(context: list, requirements: str, tier: str) -> str:
        """Генерирует запрос бюджета у клиента."""
        try:
            system_prompt = f"""Ты — дружелюбный AI-консультант "Роберт". Клиент хочет собрать ПК, но не указал бюджет.
            Твоя задача — вежливо уточнить у него максимальную сумму в тенге.

            Требования клиента: {requirements}.
            Предполагаемый сегмент: {tier}.

            Ответь кратко, вежливо и с эмодзи. Не предлагай товаров, пока не узнаешь бюджет.
            """

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(context)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=200
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error generating budget request: {e}")
            return "Я вижу, вы хотите собрать ПК! Пожалуйста, укажите ваш максимальный бюджет в тенге (например, 'до 500 000 ₸'), чтобы я мог начать подбор. 💰"

    @staticmethod
    def analyze_image(image_data: bytes, user_message: str = "") -> dict:
        """
        Анализирует изображение с помощью OpenAI Vision API.
        Распознает компоненты ПК, модели товаров из скана заказа или фото сборки.

        Args:
            image_data: Байтовые данные изображения
            user_message: Дополнительное сообщение от пользователя

        Returns:
            dict: Результат анализа с извлеченными данными
        """
        try:
            # Кодируем изображение в base64
            base64_image = base64.b64encode(image_data).decode('utf-8')

            system_prompt = """Ты - эксперт по компьютерным компонентам и электронике.
Проанализируй изображение и извлеки информацию о товарах/компонентах.

Твоя задача:
1. Распознать модели товаров (процессоры, видеокарты, материнские платы и т.д.)
2. Извлечь текст с чеков, заказов, актов
3. Определить комплектующие на фото сборки ПК
4. Извлечь названия, артикулы, количество

Формат ответа - JSON:
{
  "detected_items": [
    {
      "name": "название товара",
      "category": "категория",
      "brand": "бренд",
      "model": "модель",
      "quantity": количество
    }
  ],
  "summary": "краткое описание того, что на изображении"
}

Если на изображении НЕ компьютерные компоненты или товары, верни:
{
  "detected_items": [],
  "summary": "описание изображения",
  "not_product": true
}"""

            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_message or "Проанализируй это изображение и извлеки информацию о товарах."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=1000,
                temperature=0.3
            )

            result_text = response.choices[0].message.content.strip()

            # Очищаем от markdown если есть
            if '```' in result_text:
                result_text = re.sub(r'```json\s*|\s*```', '', result_text).strip()

            result = json.loads(result_text)
            logger.info(f"Image analysis result: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in image analysis: {e}")
            return {
                "detected_items": [],
                "summary": "Не удалось распознать товары на изображении.",
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Error analyzing image: {e}", exc_info=True)
            return {
                "detected_items": [],
                "summary": "Произошла ошибка при анализе изображения.",
                "error": str(e)
            }