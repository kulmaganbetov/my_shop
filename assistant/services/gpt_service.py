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
Твоя задача - понять намерение пользователя и извлечь параметры поиска.

ВАЖНО - РАЗЛИЧАЙ ДВА ТИПА ЗАПРОСОВ:
1. ГОТОВЫЕ УСТРОЙСТВА (intent: "product_search"):
   - Ноутбуки, смартфоны, мониторы, мыши, клавиатуры - это ГОТОВЫЕ товары
   - "нужен ноутбук", "ищу смартфон", "хочу телефон" → product_search
   - "ноутбук для игр за 500000" → product_search (category: "ноутбуки")

2. СБОРКА ПК ИЗ КОМПОНЕНТОВ (intent: "pc_build" или "pc_budget_ask"):
   - ТОЛЬКО когда клиент хочет СОБРАТЬ компьютер из отдельных частей
   - Ключевые слова: "собери ПК", "сборка компьютера", "собрать комп", "игровой ПК", "рабочий ПК"
   - "собери мне ПК за 600000" → pc_build
   - "хочу собрать компьютер" → pc_budget_ask (бюджет не указан)

НЕ ПУТАЙ:
- "ноутбук для игр" ≠ сборка ПК (это product_search для готового ноутбука!)
- "смартфон за 200000" ≠ сборка ПК (это product_search!)
- "собери игровой ПК" = сборка ПК (pc_build)

Доступные категории: смартфоны, процессоры, видеокарты, мониторы, корпуса, карты памяти, блоки питания, ноутбуки, мыши, клавиатуры, веб-камеры, внешние hdd/ssd, кабели, маршрутизаторы, коврики для мыши, коммутаторы, твердотельные диски (ssd), материнские платы

Нормализуй названия: айфон → iPhone, ртх → RTX, самсунг → Samsung

INTENTS:
- "product_search": поиск конкретного товара или категории (ноутбуки, смартфоны, комплектующие)
- "pc_build": сборка ПК с УКАЗАННЫМ бюджетом
- "pc_budget_ask": сборка ПК БЕЗ бюджета (нужно спросить)
- "faq": вопросы о магазине, доставке, оплате, гарантии
- "general": приветствия, благодарности, общение

ПАРАМЕТРЫ:
- intent: один из вышеуказанных
- category: категория товара (только для product_search)
- search_query: ключевые слова (бренд, модель)
- budget: число в тенге или null
- requirements: требования (для игр, для работы, для учебы)
- build_tier: "budget"/"mid"/"high" (только для pc_build без бюджета)
- is_detailed_query: true если просят рекомендации/сравнение/аналоги
- include_peripherals: true если к сборке ПК нужна периферия

JSON ПРИМЕРЫ:

Запрос: "нужен ноутбук для игр до 500000"
{"intent": "product_search", "category": "ноутбуки", "search_query": "игровой", "budget": 500000, "requirements": "для игр", "is_detailed_query": true, "include_peripherals": false}

Запрос: "собери мне ПК на 600000"
{"intent": "pc_build", "category": null, "search_query": "", "budget": 600000, "requirements": "", "is_detailed_query": false, "include_peripherals": false}

Запрос: "хочу собрать игровой компьютер"
{"intent": "pc_budget_ask", "category": null, "search_query": "", "budget": null, "requirements": "для игр", "build_tier": "mid", "is_detailed_query": false, "include_peripherals": false}

Запрос: "iPhone 15 Pro Max"
{"intent": "product_search", "category": "смартфоны", "search_query": "iPhone 15 Pro Max", "budget": null, "requirements": "", "is_detailed_query": false, "include_peripherals": false}

Запрос: "RTX 4070"
{"intent": "product_search", "category": "видеокарты", "search_query": "RTX 4070", "budget": null, "requirements": "", "is_detailed_query": false, "include_peripherals": false}

Ответь ТОЛЬКО JSON без текста."""
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
                           include_peripherals: bool = False,
                           category_targets: dict = None) -> dict:
        """
        Выбирает оптимальные компоненты для сборки ПК с улучшенной логикой.

        Улучшения:
        - Увеличено количество товаров для анализа (20 вместо 10)
        - Умная сортировка и фильтрация
        - Проверка совместимости компонентов
        - Балансировка CPU/GPU
        - Поддержка периферии (мониторы, мыши, клавиатуры)
        - Целевые цены для каждой категории
        """
        if category_targets is None:
            category_targets = {}

        try:
            # Подготовка компактного списка товаров для GPT
            LIMITED_PRODUCTS = {}
            LIMIT_PER_CATEGORY = 20  # Увеличили с 10 до 20

            # Определяем стратегию сортировки
            sort_reverse = (budget_tier.lower() == 'high') or (max_budget and max_budget > 500000)

            for category, products in all_products_by_category.items():
                if not products:
                    continue

                # Сортируем по цене - для бюджетной сборки выбираем ближе к целевой цене
                target_price = category_targets.get(category, 0)
                if target_price > 0:
                    # Сортируем по близости к целевой цене
                    sorted_products = sorted(
                        products,
                        key=lambda p: abs(float(p.get('credit', 0)) - target_price)
                    )
                else:
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

                # Добавляем информацию о категории с целевой ценой
                LIMITED_PRODUCTS[category] = {
                    "target_price": int(target_price) if target_price else None,
                    "products": compact_products
                }

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

            # Формируем информацию о целевых ценах
            target_prices_info = ""
            if category_targets:
                target_prices_info = "\n**ЦЕЛЕВЫЕ ЦЕНЫ ПО КАТЕГОРИЯМ:**\n"
                for cat, target in category_targets.items():
                    target_prices_info += f"- {cat}: ~{int(target):,} ₸\n"

            # Улучшенный system prompt с детальными инструкциями
            system_prompt = f"""Ты — эксперт по сборке ПК. Подбери оптимальную сборку из предоставленных компонентов.

{budget_info}
Сегмент: {budget_tier}
Требования: {user_requirements}
Периферия: {"ДА (монитор, мышь, клавиатура)" if include_peripherals else "НЕТ (только системный блок)"}
{target_prices_info}
КРИТЕРИИ ВЫБОРА:

1. **БЮДЖЕТ** (КРИТИЧНО - САМОЕ ВАЖНОЕ!):
   - Общая стоимость = сумма всех {component_count} компонентов
   - {"СУММА ВСЕХ КОМПОНЕНТОВ НЕ ДОЛЖНА превышать " + f"{max_budget:,}" + " ₸!" if max_budget else "Бюджет не задан - выбирай оптимальное соотношение цена/качество"}
   - Каждая категория имеет "target_price" - выбирай компоненты БЛИЗКО к этой цене
   - НЕЛЬЗЯ выбирать все компоненты по верхней границе - иначе превысишь бюджет
   - Если видишь, что сумма превышает бюджет - выбирай более дешевые варианты

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

ВАЖНО:
- Используй ТОЛЬКО SKU из предоставленного списка!
- {"СНАЧАЛА посчитай общую сумму выбранных компонентов, и ТОЛЬКО если она <= " + f"{max_budget:,}" + " ₸, верни JSON!" if max_budget else "Выбери компоненты близко к целевым ценам!"}"""

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
        """Выбор наиболее подходящих товаров"""
        if not products:
            return []
        
        try:
            products_to_analyze = products[:20]
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """Ты - эксперт по подбору электроники.
Проанализируй товары и выбери 3-5 наиболее подходящих для запроса пользователя.

Учитывай:
- Соответствие бюджету (используй поле **credit**)
- Соответствие требованиям пользователя
- Наличие на складе (поле stock > 0)
- Соотношение цена/качество
- Популярность бренда (Intel, AMD, Samsung и т.д.)

Верни JSON массив с SKU выбранных товаров в порядке приоритета:
["sku1", "sku2", "sku3"]

Если бюджет указан, не включай товары дороже бюджета, используя поле **credit**."""
                    },
                    {
                        "role": "user",
                        "content": f"""Запрос: {user_query}
Требования: {json.dumps(requirements, ensure_ascii=False)}

Товары:
{json.dumps(products_to_analyze, ensure_ascii=False, indent=2)}"""
                    }
                ],
                temperature=0.5,
                max_tokens=200
            )
            
            raw_content = response.choices[0].message.content.strip()
            selected_skus = []
            
            try:
                # Попытка загрузить JSON
                parsed_json = json.loads(raw_content)
                if isinstance(parsed_json, list):
                    selected_skus = parsed_json
            except json.JSONDecodeError:
                logger.warning(f"GPT returned invalid JSON for selection: {raw_content[:100]}...")
                # Fallback: Если JSON невалиден, логика автоматически перейдет в блок except,
                # где мы вернем первые 5 продуктов.
                pass
            
            
            selected_products = [p for p in products if p.get("sku") in selected_skus]
            
            # Если GPT выбрал SKU, сортируем по его порядку
            if selected_skus:
                 # Используем list.index() только если sku находится в selected_skus
                 selected_products.sort(key=lambda x: selected_skus.index(x.get("sku")) if x.get("sku") in selected_skus else len(selected_skus))
            
            # Дополнительный Fallback: Если выбранных товаров меньше 5 или GPT вернул невалидный JSON,
            # мы гарантируем, что у нас есть хотя бы 5 первых товаров.
            if not selected_products and products:
                selected_products = products[:5]

            logger.info(f"Selected {len(selected_products)} products")
            return selected_products
            
        except Exception as e:
            logger.error(f"Error selecting products: {e}")
            # В случае ЛЮБОЙ ошибки, гарантируем возврат хотя бы первых 5 товаров для генерации ответа
            return products[:5]
    
    @staticmethod
    def generate_product_response(context: list, products: list, is_detailed_query: bool = False) -> str:
        """
        Генерация ответа с рекомендацией товаров.

        Args:
            context: История сообщений
            products: Список товаров
            is_detailed_query: True если клиент просит аналоги/рекомендации, False для точных запросов
        """
        try:
            # Формируем информацию о товарах с обеими ценами
            products_info = "\n\n".join([
                f"**{p['name']}**\n"
                f"- Рассрочка: {float(p.get('credit', 0)):,} ₸\n"
                f"- Скидка (карта/наличные): {float(p.get('bonus', 0)):,} ₸\n"
                f"- Гарантия: {p.get('warranty', 'N/A')}\n"
                f"- В наличии: {'да' if int(p.get('stock', 0)) > 0 else 'нет'}"
                for p in products[:5]
            ])

            user_message = context[-1]['content']

            # Адаптивный промпт в зависимости от типа запроса
            if is_detailed_query:
                system_prompt = """Ты - эксперт-консультант по электронике в интернет-магазине.
Клиент просит аналоги или рекомендации. Дай развернутый ответ.

Твой ответ должен:
1. Кратко подтвердить понимание запроса
2. Представить 2-3 варианта с объяснением преимуществ каждого
3. Дать конкретную рекомендацию

Для каждого товара ОБЯЗАТЕЛЬНО указывай:
- Название
- Две цены: рассрочка (по умолчанию) и скидка (при оплате картой/наличными)
- Гарантию
- Ключевые преимущества

Формат:
- Используй эмодзи для визуальности (✅, 💰, ⚡, 🎮)
- Выдели ключевые преимущества
- Укажи для кого подходит каждый вариант

Будь дружелюбным и профессиональным."""
            else:
                system_prompt = """Ты - консультант интернет-магазина электроники.
Клиент спрашивает конкретную модель. Дай КОРОТКИЙ и ТОЧНЫЙ ответ.

Формат ответа (БЕЗ ЛИШНИХ СЛОВ):

**[Название товара]**
💳 Рассрочка: [цена] ₸
💰 Скидка: [цена] ₸ (при оплате картой/наличными)
🛡️ Гарантия: [период]

Если товаров несколько - покажи ТОП-2 в таком же формате.
Одно предложение в конце, если нужно пояснить разницу между моделями.

НЕ пиши длинные описания! НЕ добавляй количество на складе!"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(context[:-1])

            messages.append({
                "role": "user",
                "content": f"""Запрос клиента: {user_message}

Найденные товары:
{products_info}

{"Помоги выбрать лучший вариант с пояснениями." if is_detailed_query else "Дай короткий ответ с названием и ценами."}"""
            })

            # Адаптивный max_tokens
            max_tokens = 800 if is_detailed_query else 400

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=max_tokens
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error generating product response: {e}", exc_info=True)
            return "Извините, произошла ошибка при формировании ответа."
    
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