# assistant/views.py
import json
import logging
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import uuid 
import re

from .services import GPTService, ProductSearchService, FAQHandler
from .models import ChatSession, ChatMessage

logger = logging.getLogger('assistant')

# Максимальное количество сообщений для контекста (например, 10 последних сообщений, включая user и assistant)
MAX_HISTORY_MESSAGES = 10 

def chat_page(request):
    """Страница чата с ассистентом"""
    return render(request, 'assistant/chat.html')


import json
import logging
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import uuid 
import re # Необходим для извлечения SKU

from .services import GPTService, ProductSearchService, FAQHandler
from .models import ChatSession, ChatMessage

logger = logging.getLogger('assistant')

# Максимальное количество сообщений для контекста (например, 10 последних сообщений, включая user и assistant)
MAX_HISTORY_MESSAGES = 10 
# (Предполагается, что другие функции views.py и константы объявлены выше)


@csrf_exempt
@require_http_methods(["POST"])
def chat_assistant(request):
    """
    Основной API endpoint для чата с ассистентом
    
    Обрабатывает сообщения, определяет намерение (intent), ищет товары (с fallback), 
    и генерирует ответ с учетом истории чата (context).
    """
    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()
        session_id = data.get("session_id")
        
        if not user_message:
            return JsonResponse({
                "success": False,
                "error": "Сообщение не может быть пустым"
            }, status=400)
        
        logger.info(f"Received message: {user_message[:100]}")
        
        # Создаем или получаем сессию
        if session_id:
            try:
                session = ChatSession.objects.get(session_id=session_id)
            except ChatSession.DoesNotExist:
                session = ChatSession.objects.create(session_id=session_id)
        else:
            session = ChatSession.objects.create(session_id=str(uuid.uuid4()))
        
        
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
        
        # Сохраняем сообщение пользователя (в базу)
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
        if intent == "product_search":
            category = analysis.get("category", "")
            search_query = analysis.get("search_query", "").strip() 
            budget = analysis.get("budget")
            
            logger.info(f"Searching products: category={category}, query={search_query}")
            
            # --- 1. Основной поиск (с запросом и категорией) ---
            products = ProductSearchService.search(
                query=search_query,
                category=category
            )
            
            # --- 2. Запасной поиск (Fallback Strategy) ---
            # Fallback только для ОБЫЧНЫХ запросов, не для прямого SKU (который и так точен)
            if not products and category and search_query and not forced_sku:
                logger.warning(f"Primary search failed (q='{search_query}'). Retrying search using only category.")
                products = ProductSearchService.search(
                    query="", # Очищаем ограничивающий запрос
                    category=category 
                )
            
            # ----------------------------------------------------
            
            # Фильтруем по бюджету если указан
            if budget and products:
                products = ProductSearchService.filter_by_price(products, budget)
                logger.info(f"Filtered by budget {budget}: {len(products)} products")
            
            # Фильтруем только товары в наличии
            products = ProductSearchService.filter_in_stock(products)
            
            if products:
                # ШАГ 3: Выбираем лучшие товары через GPT
                requirements = {
                    "budget": budget,
                    "requirements": analysis.get("requirements", "")
                }
                
                selected_products = GPTService.select_best_products(
                    products, 
                    user_message, 
                    requirements
                )
                
                # ШАГ 4: Генерируем ответ с рекомендациями
                response_text = GPTService.generate_product_response(
                    current_context,
                    selected_products
                )
                
                products = selected_products[:5]
                
            else:
                response_text = """К сожалению, по вашему запросу не найдено подходящих товаров в наличии. 😔

Попробуйте:
• Изменить бюджет
• Выбрать другую категорию товаров
• Связаться с нами для индивидуальной консультации: +7 (777) 123-45-67"""



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

            logger.info(f"PC Build requested: tier={build_tier}, reqs={user_requirements}, budget={budget}")
            
            # 1. Получаем все необходимые компоненты из БД
            all_products_by_category = ProductSearchService.get_components_for_build()
            
            required_categories = ["процессоры", "видеокарты", "материнские платы", "корпуса", "блоки питания", "твердотельные диски (ssd)"]
            
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
                        build_tier
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

                # 4. Генерируем финальный ответ
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
            intent=intent
        )
        
        logger.info(f"Response generated successfully. Intent: {intent}, Products: {len(products)}")
        
        # Возвращаем ответ
        return JsonResponse({
            "success": True,
            "response": response_text,
            "products": products,
            "intent": intent,
            "session_id": session.session_id
        })
        
    except json.JSONDecodeError:
        logger.error("Invalid JSON in request")
        return JsonResponse({
            "success": False,
            "error": "Неверный формат данных"
        }, status=400)
        
    except Exception as e:
        logger.error(f"Error in chat_assistant: {str(e)}", exc_info=True)
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