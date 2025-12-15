# assistant/views.py
import json
import logging
import time
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import uuid
import re

from .services import GPTService, ProductSearchService, FAQHandler
from .models import ChatSession, ChatMessage, AssistantLog

logger = logging.getLogger('assistant')

# Максимальное количество сообщений для контекста
MAX_HISTORY_MESSAGES = 10


def chat_page(request):
    """Страница чата с ассистентом"""
    return render(request, 'assistant/chat.html')


def log_event(session, log_type, message, severity='info', **kwargs):
    """Утилита для логирования событий"""
    try:
        AssistantLog.objects.create(
            session=session,
            log_type=log_type,
            severity=severity,
            message=message,
            **kwargs
        )
    except Exception as e:
        logger.error(f"Failed to log event: {e}")


@csrf_exempt
@require_http_methods(["POST"])
def chat_assistant(request):
    """
    Основной API endpoint для чата с ассистентом

    Обрабатывает сообщения, определяет намерение (intent), ищет товары (с fallback),
    и генерирует ответ с учетом истории чата (context).
    Поддерживает загрузку файлов (изображения, PDF, Excel).
    """
    start_time = time.time()

    try:
        # Определяем тип запроса по Content-Type
        content_type = request.content_type or ''

        if 'multipart/form-data' in content_type:
            # Обработка FormData (с файлами)
            uploaded_file = request.FILES.get('file')
            user_message = request.POST.get("message", "").strip()
            session_id = request.POST.get("session_id")
        elif 'application/json' in content_type:
            # Обработка JSON
            uploaded_file = None
            data = json.loads(request.body)
            user_message = data.get("message", "").strip()
            session_id = data.get("session_id")
        else:
            # Fallback: пробуем как FormData
            uploaded_file = request.FILES.get('file') if request.FILES else None
            if request.POST:
                user_message = request.POST.get("message", "").strip()
                session_id = request.POST.get("session_id")
            else:
                # Пробуем JSON
                data = json.loads(request.body)
                user_message = data.get("message", "").strip()
                session_id = data.get("session_id")

        if not user_message and not uploaded_file:
            return JsonResponse({
                "success": False,
                "error": "Сообщение или файл должны быть предоставлены"
            }, status=400)

        logger.info(f"Received message: {user_message[:100] if user_message else 'File upload'}")

        # Создаем или получаем сессию
        if session_id:
            try:
                session = ChatSession.objects.get(session_id=session_id)
            except ChatSession.DoesNotExist:
                session = ChatSession.objects.create(session_id=session_id)
                log_event(session, 'session_start', 'Новая сессия создана')
        else:
            session = ChatSession.objects.create(session_id=str(uuid.uuid4()))
            log_event(session, 'session_start', 'Новая сессия создана')

        # Логируем вопрос пользователя
        log_event(session, 'user_question', 'Вопрос пользователя', user_input=user_message)

        # Проверяем, находится ли сессия в режиме "с менеджером"
        if session.status == 'with_manager':
            # Если клиент пишет в режиме менеджера - сохраняем и уведомляем
            ChatMessage.objects.create(
                session=session,
                message=user_message,
                is_user=True,
                sender_type='user'
            )
            return JsonResponse({
                "success": True,
                "response": "Ваше сообщение отправлено менеджеру. Ожидайте ответа.",
                "products": [],
                "intent": "with_manager",
                "session_id": session.session_id,
                "with_manager": True
            })
        
        
        # ------------------------------------------------------------------
        # НОВОЕ: ПРОВЕРКА НА ПРЯМОЙ ЗАПРОС ПО SKU (Хочу заказать SKU: 47442)
        forced_sku = None
        # Ищем 1 или более цифр после слова "SKU" и необязательных символов (: или пробел)
        sku_match = re.search(r'sku[:\s]*(\d+)', user_message.lower())
        
        if sku_match:
            # Извлекаем только чистый SKU
            forced_sku = sku_match.group(1).strip()
            logger.info(f"Forced SKU detected: {forced_sku}")
        # ------------------------------------------------------------------

        # ------------------------------------------------------------------
        # 1. Формирование контекста для GPT
        
        history = []
        db_messages = session.messages.all().order_by('-timestamp')[:MAX_HISTORY_MESSAGES]
        
        for msg in reversed(db_messages):
            history.append({
                "role": "user" if msg.is_user else "assistant",
                "content": msg.message
            })

        # Добавляем текущее сообщение пользователя в конец контекста
        current_context = history + [{
            "role": "user",
            "content": user_message
        }]
        # ------------------------------------------------------------------
        
        # Обработка загруженного файла (если есть)
        image_analysis = None
        attachment_path = None

        if uploaded_file:
            # Проверяем тип файла
            file_type = uploaded_file.content_type
            logger.info(f"File uploaded: {uploaded_file.name}, type: {file_type}")

            # Сохраняем сообщение пользователя с вложением
            chat_message = ChatMessage.objects.create(
                session=session,
                message=user_message or "Загружено изображение",
                is_user=True,
                attachment=uploaded_file,
                attachment_type=file_type
            )
            attachment_path = chat_message.attachment.path

            # Если это изображение, анализируем его
            if file_type.startswith('image/'):
                image_data = uploaded_file.read()
                image_analysis = GPTService.analyze_image(image_data, user_message)
                logger.info(f"Image analysis completed: {image_analysis.get('summary', 'N/A')}")

                # Если распознаны товары, формируем запрос для поиска
                if image_analysis.get('detected_items'):
                    detected_names = [item.get('name', '') for item in image_analysis['detected_items']]
                    user_message = f"Найди товары: {', '.join(detected_names)}"
                    logger.info(f"Generated search query from image: {user_message}")
        else:
            # Сохраняем сообщение пользователя (в базу) без вложения
            ChatMessage.objects.create(
                session=session,
                message=user_message,
                is_user=True
            )

        # ШАГ 1: Анализируем запрос через GPT или используем принудительный SKU
        if forced_sku:
            # Имитируем результат анализа GPT для прямого поиска по SKU
            analysis = {
                "intent": "product_search", 
                "category": "",
                "search_query": forced_sku, 
                "budget": None,
                "requirements": "детальный просмотр/заказ по SKU"
            }
        else:
            analysis = GPTService.analyze_query(current_context)
            
        intent = analysis.get("intent", "general")
        
        logger.info(f"Intent detected: {intent}")
        
        products = []
        response_text = ""
        
        # ШАГ 2: Обрабатываем в зависимости от намерения

        # --- FOLLOW-UP: уточнение предыдущего поиска ---
        if intent == "follow_up":
            follow_up_type = analysis.get("follow_up_type", "more_options")
            search_ctx = session.search_context

            if not search_ctx:
                response_text = "Сначала выберите категорию товаров. Например: 'ноутбуки до 500000' или 'процессор AMD'"
            else:
                # Восстанавливаем параметры предыдущего поиска
                category = search_ctx.get("category", "")
                search_query = search_ctx.get("search_query", "")
                prev_min = search_ctx.get("min_price", 0)
                prev_max = search_ctx.get("max_price")
                shown_prices = search_ctx.get("shown_prices", [])

                logger.info(f"Follow-up '{follow_up_type}': prev range {prev_min}-{prev_max}, shown: {shown_prices}")

                # Модифицируем диапазон цен в зависимости от типа запроса
                if follow_up_type == "more_expensive" and shown_prices:
                    # "Дороже" - ищем выше максимальной показанной цены
                    new_min = max(shown_prices) + 1
                    new_max = prev_max  # Не превышаем исходный бюджет
                    if new_min >= new_max:
                        response_text = f"К сожалению, в вашем бюджете до {int(prev_max):,}₸ нет товаров дороже. Хотите увеличить бюджет?"
                        products = []
                    else:
                        products = ProductSearchService.search(query=search_query, category=category)
                        products = ProductSearchService.filter_in_stock(products)
                        products = ProductSearchService.filter_by_price(products, max_price=new_max, min_price=new_min)
                        products = sorted(products, key=lambda p: float(p.get('credit', 0)), reverse=True)

                elif follow_up_type == "cheaper" and shown_prices:
                    # "Дешевле" - ищем ниже минимальной показанной цены
                    new_max = min(shown_prices) - 1
                    new_min = prev_min * 0.5 if prev_min else 0
                    if new_max <= new_min:
                        response_text = f"Нет товаров дешевле {int(min(shown_prices)):,}₸ в этой категории."
                        products = []
                    else:
                        products = ProductSearchService.search(query=search_query, category=category)
                        products = ProductSearchService.filter_in_stock(products)
                        products = ProductSearchService.filter_by_price(products, max_price=new_max, min_price=new_min)
                        products = sorted(products, key=lambda p: float(p.get('credit', 0)), reverse=True)

                else:  # more_options
                    # "Ещё варианты" - исключаем уже показанные SKU
                    shown_skus = search_ctx.get("shown_skus", [])
                    products = ProductSearchService.search(query=search_query, category=category)
                    products = ProductSearchService.filter_in_stock(products)
                    if prev_max:
                        products = ProductSearchService.filter_by_price(products, max_price=prev_max, min_price=prev_min)
                    # Исключаем уже показанные
                    products = [p for p in products if p.get("sku") not in shown_skus]
                    products = sorted(products, key=lambda p: float(p.get('credit', 0)), reverse=True)

                # Генерируем ответ если есть товары
                if products:
                    selected_products = products[:3]

                    # Обновляем контекст
                    new_prices = [float(p.get('credit', 0)) for p in selected_products]
                    new_skus = [p.get('sku') for p in selected_products]
                    session.search_context = {
                        **search_ctx,
                        "shown_prices": shown_prices + new_prices,
                        "shown_skus": search_ctx.get("shown_skus", []) + new_skus
                    }
                    session.save()

                    response_text = GPTService.generate_product_response(current_context, selected_products)
                    products = selected_products
                elif not response_text:
                    response_text = "К сожалению, больше вариантов в этом диапазоне нет. Попробуйте изменить бюджет."

        # --- PRODUCT_SEARCH: новый поиск ---
        elif intent == "product_search":
            category = analysis.get("category", "")
            search_query = analysis.get("search_query", "").strip()
            budget = analysis.get("budget")

            logger.info(f"Searching products: category={category}, query={search_query}, budget={budget}")

            # --- 1. Основной поиск ---
            products = ProductSearchService.search(query=search_query, category=category)

            # --- 2. Fallback ---
            if not products and category and search_query and not forced_sku:
                logger.warning(f"Primary search failed. Retrying with category only.")
                products = ProductSearchService.search(query="", category=category)

            products = ProductSearchService.filter_in_stock(products)

            # --- 3. Умная фильтрация по бюджету ---
            min_price = 0
            max_price = None
            if budget and products:
                budget = float(budget)
                max_price = budget
                min_price = budget * 0.5
                products = ProductSearchService.filter_by_price(products, max_price=max_price, min_price=min_price)
                logger.info(f"Filtered by budget range {min_price:.0f}-{budget:.0f}: {len(products)} products")

                if not products:
                    products = ProductSearchService.filter_in_stock(
                        ProductSearchService.search(query=search_query, category=category)
                    )
                    min_price = budget * 0.3
                    products = ProductSearchService.filter_by_price(products, max_price=max_price, min_price=min_price)

                products = sorted(products, key=lambda p: float(p.get('credit', 0)), reverse=True)

            if products:
                requirements = {"budget": budget, "requirements": analysis.get("requirements", "")}
                selected_products = GPTService.select_best_products(products, user_message, requirements)
                is_detailed_query = analysis.get("is_detailed_query", False)
                response_text = GPTService.generate_product_response(current_context, selected_products, is_detailed_query)
                products = selected_products[:5]

                # --- СОХРАНЯЕМ КОНТЕКСТ ПОИСКА ---
                shown_prices = [float(p.get('credit', 0)) for p in products]
                shown_skus = [p.get('sku') for p in products]
                session.search_context = {
                    "category": category,
                    "search_query": search_query,
                    "min_price": min_price,
                    "max_price": max_price,
                    "shown_prices": shown_prices,
                    "shown_skus": shown_skus
                }
                session.save()
                logger.info(f"Saved search context: {session.search_context}")

            else:
                response_text = f"""К сожалению, не найдено товаров по запросу.

Попробуйте:
• Изменить бюджет
• Уточнить категорию
• Связаться с консультантом"""



        elif intent == "pc_budget_ask":
            user_requirements = analysis.get("requirements", "универсальная сборка")
            build_tier = analysis.get("build_tier", "mid")
            
            response_text = GPTService.generate_budget_request(
                current_context,
                user_requirements,
                build_tier
            )



# ------------------------------------------------------------------
        # НОВОЕ: ОБРАБОТКА СБОРКИ ПК
        # ------------------------------------------------------------------
        elif intent == "pc_build":
            user_requirements = analysis.get("requirements", "универсальная сборка")
            build_tier = analysis.get("build_tier", "mid").lower()
            budget = analysis.get("budget")
            include_peripherals = analysis.get("include_peripherals", False)

            logger.info(f"PC Build requested: tier={build_tier}, reqs={user_requirements}, budget={budget}, peripherals={include_peripherals}")

            # 1. Получаем все необходимые компоненты из БД с умной фильтрацией
            all_products_by_category, category_targets = ProductSearchService.get_components_for_build(
                budget=budget,
                tier=build_tier,
                include_peripherals=include_peripherals
            )

            # Базовые категории (системный блок)
            required_categories = ["процессоры", "видеокарты", "материнские платы", "корпуса", "блоки питания", "твердотельные диски (ssd)"]

            # Добавляем периферию если запрошена
            if include_peripherals:
                required_categories.extend(["мониторы", "мыши", "клавиатуры"])
            
            # Проверка наличия всех компонентов
            missing_components = [c for c in required_categories if c not in all_products_by_category or not all_products_by_category[c]]
            
            logger.info(f"Products available for build: {len(all_products_by_category)} categories found.")
            if missing_components:
                logger.error(f"FATAL: Missing essential categories: {missing_components}")

            if missing_components:
                # ------------------------------------------------------------------
                # 1. FALLBACK: Не удалось собрать ПК -> Рекомендуем товары в отсутствующей категории
                # ------------------------------------------------------------------
                
                fallback_category = missing_components[0]
                
                # Получаем все товары в отсутствующей категории (в наличии и не в наличии)
                # Это позволяет нам показать пользователю, что товар существует, но временно отсутствует
                fallback_products = ProductSearchService.search(
                    query=user_requirements or "", # используем требования пользователя
                    category=fallback_category,
                    limit=50 # Ограничиваем для GPT
                )
                
                if fallback_products:
                    # Генерируем ответ, объясняя сбой и предлагая альтернативу
                    response_text = f"""😔 Извините, но я не могу собрать ПК прямо сейчас. 
                    В наличии отсутствуют следующие обязательные компоненты: **{', '.join(missing_components)}** (например, {fallback_category}).
                    
                    Однако, я могу предложить вам **лучшие {fallback_category}** по вашим требованиям:"""

                    requirements_data = {
                        "budget": None,
                        "requirements": user_requirements
                    }
                    
                    selected_products = GPTService.select_best_products(
                        fallback_products, 
                        user_message, 
                        requirements_data
                    )
                    
                    # Генерируем ответ с рекомендациями
                    response_text += GPTService.generate_product_response(
                        current_context,
                        selected_products
                    )
                    
                    products = selected_products[:5]
                else:
                    # Если даже по одной категории ничего не нашли
                    response_text = f"""😔 Извините, но я не смог собрать ПК. Произошла ошибка при поиске: в базе магазина полностью отсутствуют товары категории **{fallback_category}**. Пожалуйста, попробуйте изменить запрос или свяжитесь с поддержкой."""
                    
            else:
                # ------------------------------------------------------------------
                # 2. УСПЕШНАЯ СБОРКА
                # ------------------------------------------------------------------
                
                # 2. GPT выбирает лучшие компоненты и проверяет совместимость
                try:
                    selected_skus_by_category = GPTService.select_pc_components(
                        all_products_by_category,
                        user_requirements,
                        build_tier,
                        max_budget=budget,
                        include_peripherals=include_peripherals,
                        category_targets=category_targets
                    )
                except Exception as e:
                    logger.error(f"GPT component selection failed: {e}", exc_info=True)
                    selected_skus_by_category = {} # В случае ошибки GPT возвращаем пустой словарь
                
                logger.info(f"GPT returned {len(selected_skus_by_category)} selected components.")

                # 3. Собираем детали выбранных SKU для финального ответа
                selected_build_details = {}
                
                # Проверяем, что GPT вернул все 6 категорий
                if len(selected_skus_by_category) == len(required_categories):
                    for category, sku in selected_skus_by_category.items():
                        
                        # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: ИЩЕМ В API ПО SKU ---
                        product_detail = ProductSearchService.get_by_sku(sku) # Точечный запрос к API
                        
                        if product_detail:
                            selected_build_details[category] = product_detail
                        else:
                            # Если API не подтвердил SKU, прерываем
                            logger.error(f"SKU '{sku}' returned by GPT not found in API. Aborting build.")
                            selected_build_details = {} 
                            break 

                # 4. Пост-валидация бюджета
                if len(selected_build_details) == len(required_categories):
                    total_price = sum(float(item.get('credit', 0)) for item in selected_build_details.values())

                    if budget and total_price > budget:
                        overage_percent = ((total_price - budget) / budget) * 100
                        logger.warning(f"Build exceeds budget by {overage_percent:.1f}%: {total_price:,.0f} ₸ vs {budget:,} ₸")

                        # Если превышение больше 15%, пытаемся найти более дешёвые альтернативы
                        if overage_percent > 15:
                            logger.info("Attempting to find cheaper alternatives...")
                            # Пересортировываем и выбираем более дешевые варианты
                            for category in selected_build_details.keys():
                                if category in all_products_by_category:
                                    current_price = float(selected_build_details[category].get('credit', 0))
                                    target = category_targets.get(category, current_price)

                                    # Ищем более дешевую альтернативу близко к целевой цене
                                    cheaper_options = [
                                        p for p in all_products_by_category[category]
                                        if float(p.get('credit', 0)) <= target * 1.1 and float(p.get('credit', 0)) < current_price
                                    ]
                                    if cheaper_options:
                                        # Берем ближайший к целевой цене
                                        cheaper_options.sort(key=lambda p: abs(float(p.get('credit', 0)) - target))
                                        selected_build_details[category] = cheaper_options[0]
                                        logger.info(f"Replaced {category}: {current_price:,.0f} -> {float(cheaper_options[0].get('credit', 0)):,.0f} ₸")

                            # Пересчитываем сумму
                            total_price = sum(float(item.get('credit', 0)) for item in selected_build_details.values())
                            logger.info(f"After optimization: {total_price:,.0f} ₸")

                # 5. Генерируем финальный ответ
                if len(selected_build_details) == len(required_categories):
                    response_text = GPTService.generate_pc_build_response(
                        current_context,
                        selected_build_details
                    )
                    # При успешной сборке, вернем список деталей для отрисовки
                    products = list(selected_build_details.values())
                else:
                    response_text = """К сожалению, не удалось подобрать **совместимую сборку** или GPT не вернул полный комплект компонентов. 
                    
                    Возможные причины:
                    1. Несовместимость выбранных GPT комплектующих.
                    2. В базе нет компонентов, удовлетворяющих вашим требованиям и совместимости.
                    
                    Попробуйте изменить требования к ПК или свяжитесь с консультантом."""

        
        elif intent == "faq":
            # Обработка FAQ
            faq_context = FAQHandler.get_all_faq_context()
            
            direct_answer = FAQHandler.find_relevant_faq(user_message)
            
            if direct_answer:
                response_text = direct_answer
            else:
                # Генерируем ответ через GPT с контекстом FAQ
                response_text = GPTService.generate_faq_response(
                    current_context,
                    faq_context
                )
        
        else:
            # Общение
            response_text = GPTService.generate_general_response(current_context)
        
        # Сохраняем ответ ассистента
        ChatMessage.objects.create(
            session=session,
            message=response_text,
            is_user=False,
            sender_type='bot',
            intent=intent
        )

        # Вычисляем время ответа
        response_time = int((time.time() - start_time) * 1000)

        # Логируем ответ бота
        log_event(
            session, 'bot_response', 'Ответ бота',
            user_input=user_message,
            bot_output=response_text[:500],
            intent=intent,
            response_time_ms=response_time
        )

        logger.info(f"Response generated successfully. Intent: {intent}, Products: {len(products)}, Time: {response_time}ms")

        # Возвращаем ответ
        return JsonResponse({
            "success": True,
            "response": response_text,
            "products": products,
            "intent": intent,
            "session_id": session.session_id,
            "with_manager": session.status in ['pending_manager', 'with_manager']
        })

    except json.JSONDecodeError:
        logger.error("Invalid JSON in request")
        return JsonResponse({
            "success": False,
            "error": "Неверный формат данных"
        }, status=400)

    except Exception as e:
        logger.error(f"Error in chat_assistant: {str(e)}", exc_info=True)

        # Логируем ошибку
        if 'session' in locals():
            log_event(
                session, 'error', f'Ошибка обработки: {str(e)}',
                severity='error',
                error_details=str(e)
            )

        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка при обработке запроса. Попробуйте еще раз."
        }, status=500)


@require_http_methods(["GET"])
def get_product_details(request, sku):
    """
    Получение детальной информации о товаре
    ...
    """
    try:
        logger.info(f"Fetching product details for SKU: {sku}")
        
        product = ProductSearchService.get_by_sku(sku)
        
        if product:
            return JsonResponse({
                "success": True,
                "product": product
            })
        else:
            return JsonResponse({
                "success": False,
                "error": "Товар не найден"
            }, status=404)
            
    except Exception as e:
        logger.error(f"Error in get_product_details: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка при получении данных о товаре"
        }, status=500)


@require_http_methods(["GET"])
def get_chat_history(request, session_id):
    """
    Получение истории чата
    ...
    """
    try:
        session = ChatSession.objects.get(session_id=session_id)
        # Убедимся, что мы берем только те сообщения, которые нужно показать
        messages = session.messages.all().order_by('timestamp')
        
        messages_data = [
            {
                "message": msg.message,
                "is_user": msg.is_user,
                "timestamp": msg.timestamp.isoformat(),
                "intent": msg.intent
            }
            for msg in messages
        ]
        
        return JsonResponse({
            "success": True,
            "messages": messages_data
        })
        
    except ChatSession.DoesNotExist:
        return JsonResponse({
            "success": False,
            "error": "Сессия не найдена"
        }, status=404)
        
    except Exception as e:
        logger.error(f"Error in get_chat_history: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка при получении истории"
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def request_manager(request):
    """
    API для запроса помощи менеджера
    Клиент может вызвать менеджера если бот не справился
    """
    try:
        data = json.loads(request.body)
        session_id = data.get("session_id")
        reason = data.get("reason", "Клиент запросил помощь менеджера")

        if not session_id:
            return JsonResponse({
                "success": False,
                "error": "session_id обязателен"
            }, status=400)

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return JsonResponse({
                "success": False,
                "error": "Сессия не найдена"
            }, status=404)

        # Обновляем статус сессии
        session.status = 'pending_manager'
        session.save()

        # Логируем передачу менеджеру
        log_event(
            session, 'manager_handoff',
            'Клиент запросил помощь менеджера',
            handoff_reason=reason
        )

        # Добавляем системное сообщение
        system_message = "🔔 Вы запросили помощь менеджера. Ожидайте, скоро с вами свяжется наш специалист."
        ChatMessage.objects.create(
            session=session,
            message=system_message,
            is_user=False,
            sender_type='bot',
            intent='manager_handoff'
        )

        return JsonResponse({
            "success": True,
            "message": system_message,
            "status": "pending_manager"
        })

    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "error": "Неверный формат данных"
        }, status=400)

    except Exception as e:
        logger.error(f"Error in request_manager: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка"
        }, status=500)


@require_http_methods(["GET"])
def get_new_messages(request, session_id):
    """
    API для получения новых сообщений (используется клиентом для получения ответов менеджера)
    """
    try:
        session = ChatSession.objects.get(session_id=session_id)
        last_id = request.GET.get('last_id', 0)

        # Получаем новые сообщения после last_id
        messages = session.messages.filter(pk__gt=last_id).order_by('timestamp')

        messages_data = []
        for msg in messages:
            messages_data.append({
                'id': msg.pk,
                'message': msg.message,
                'is_user': msg.is_user,
                'sender_type': msg.sender_type,
                'timestamp': msg.timestamp.isoformat(),
            })

        return JsonResponse({
            "success": True,
            "messages": messages_data,
            "session_status": session.status,
            "with_manager": session.status in ['pending_manager', 'with_manager']
        })

    except ChatSession.DoesNotExist:
        return JsonResponse({
            "success": False,
            "error": "Сессия не найдена"
        }, status=404)

    except Exception as e:
        logger.error(f"Error in get_new_messages: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка"
        }, status=500)


@require_http_methods(["GET"])
def get_session_status(request, session_id):
    """
    API для проверки статуса сессии
    """
    try:
        session = ChatSession.objects.get(session_id=session_id)

        return JsonResponse({
            "success": True,
            "status": session.status,
            "with_manager": session.status in ['pending_manager', 'with_manager'],
            "manager_name": session.manager.get_full_name() if session.manager else None
        })

    except ChatSession.DoesNotExist:
        return JsonResponse({
            "success": False,
            "error": "Сессия не найдена"
        }, status=404)

    except Exception as e:
        logger.error(f"Error in get_session_status: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Произошла ошибка"
        }, status=500)