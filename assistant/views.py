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
    """
    Утилита для логирования событий в БД и файловый лог.

    УЛУЧШЕНО: Детальное логирование с контекстом.
    """
    try:
        # Логируем в БД
        AssistantLog.objects.create(
            session=session,
            log_type=log_type,
            severity=severity,
            message=message,
            **kwargs
        )

        # Дублируем в файловый лог с дополнительным контекстом
        session_id = session.session_id if session else 'NO_SESSION'
        extra_info = ', '.join(f"{k}={v}" for k, v in kwargs.items() if v) if kwargs else ''

        log_msg = f"[{log_type.upper()}] session={session_id[:8]}... | {message}"
        if extra_info:
            log_msg += f" | {extra_info}"

        if severity == 'error':
            logger.error(log_msg)
        elif severity == 'warning':
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    except Exception as e:
        logger.error(f"[LOG_EVENT] Failed to log event: {e}", exc_info=True)


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
        # Проверяем, есть ли файл в запросе
        uploaded_file = request.FILES.get('file')

        if uploaded_file:
            # Обработка FormData
            user_message = request.POST.get("message", "").strip()
            session_id = request.POST.get("session_id")
        else:
            # Обработка JSON
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
            # Если клиент пишет в режиме менеджера - только сохраняем сообщение
            # БЕЗ автоматического ответа (убрано по запросу)
            ChatMessage.objects.create(
                session=session,
                message=user_message,
                is_user=True,
                sender_type='user'
            )
            logger.info(f"Message saved for manager session: {session.session_id}")

            # Возвращаем пустой ответ - сообщение просто сохранено
            return JsonResponse({
                "success": True,
                "response": "",  # Пустой ответ - без автоматического уведомления
                "products": [],
                "intent": "with_manager",
                "session_id": session.session_id,
                "with_manager": True,
                "message_saved": True  # Флаг для фронтенда
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
        if intent == "product_search":
            category = analysis.get("category", "")
            search_query = analysis.get("search_query", "").strip()
            budget = analysis.get("budget")
            is_detailed_query = analysis.get("is_detailed_query", False)

            logger.info(f"[PRODUCT_SEARCH] query='{search_query}', category='{category}', "
                       f"budget={budget}, detailed={is_detailed_query}")

            # Используем умный поиск с fallback стратегиями
            if forced_sku:
                # Прямой поиск по SKU
                product = ProductSearchService.get_by_sku(forced_sku)
                products = [product] if product else []
                logger.info(f"[PRODUCT_SEARCH] Direct SKU lookup: {forced_sku}, found={bool(product)}")
            else:
                # Умный поиск с fallback
                products = ProductSearchService.search_with_fallback(
                    query=search_query,
                    category=category,
                    budget=budget
                )

            # Фильтруем по бюджету (если еще не отфильтровано)
            if budget and products and not forced_sku:
                products = ProductSearchService.filter_by_price(products, budget)
                logger.info(f"[PRODUCT_SEARCH] After budget filter: {len(products)} products")

            # Фильтруем по наличию
            products = ProductSearchService.filter_in_stock(products)
            logger.info(f"[PRODUCT_SEARCH] After stock filter: {len(products)} products")

            if products:
                # Выбираем лучшие товары через GPT
                requirements = {
                    "budget": budget,
                    "requirements": analysis.get("requirements", "")
                }

                selected_products = GPTService.select_best_products(
                    products,
                    user_message,
                    requirements
                )

                # Генерируем ответ с точными ценами
                response_text = GPTService.generate_product_response(
                    current_context,
                    selected_products,
                    is_detailed_query=is_detailed_query
                )

                products = selected_products[:5]
                logger.info(f"[PRODUCT_SEARCH] Final selection: {len(products)} products")

            else:
                logger.warning(f"[PRODUCT_SEARCH] No products found for query='{search_query}'")
                response_text = """К сожалению, по вашему запросу не найдено подходящих товаров в наличии. 😔

Попробуйте:
• Изменить бюджет или категорию
• Уточнить название товара
• Связаться с нами: +7 (777) 123-45-67"""



        elif intent == "pc_budget_ask":
            user_requirements = analysis.get("requirements", "универсальная сборка")
            build_tier = analysis.get("build_tier", "mid")
            
            response_text = GPTService.generate_budget_request(
                current_context,
                user_requirements,
                build_tier
            )



        # ------------------------------------------------------------------
        # ОБРАБОТКА СБОРКИ ПК (УЛУЧШЕНО)
        # ------------------------------------------------------------------
        elif intent == "pc_build":
            user_requirements = analysis.get("requirements", "универсальная сборка")
            build_tier = analysis.get("build_tier", "mid").lower()
            budget = analysis.get("budget")
            include_peripherals = analysis.get("include_peripherals", False)

            logger.info(f"[PC_BUILD] Starting: tier={build_tier}, budget={budget}, "
                       f"peripherals={include_peripherals}, requirements='{user_requirements}'")

            # 1. Получаем все компоненты
            all_products_by_category = ProductSearchService.get_components_for_build(
                budget=budget,
                tier=build_tier,
                include_peripherals=include_peripherals
            )

            # Определяем требуемые категории
            required_categories = [
                "процессоры", "видеокарты", "материнские платы",
                "корпуса", "блоки питания", "твердотельные диски (ssd)"
            ]
            if include_peripherals:
                required_categories.extend(["мониторы", "мыши", "клавиатуры"])

            # Проверка наличия компонентов
            missing_components = [
                c for c in required_categories
                if c not in all_products_by_category or not all_products_by_category[c]
            ]

            logger.info(f"[PC_BUILD] Found {len(all_products_by_category)}/{len(required_categories)} categories")

            if missing_components:
                logger.error(f"[PC_BUILD] Missing categories: {missing_components}")

                # FALLBACK: Предлагаем товары из отсутствующей категории
                fallback_category = missing_components[0]
                fallback_products = ProductSearchService.search(
                    query="",
                    category=fallback_category,
                    limit=50
                )

                if fallback_products:
                    response_text = f"""😔 К сожалению, не удалось собрать полную конфигурацию ПК.

**Отсутствуют в наличии:** {', '.join(missing_components)}

Вот лучшие варианты из категории **{fallback_category}**:"""

                    selected_products = GPTService.select_best_products(
                        fallback_products,
                        user_message,
                        {"budget": budget, "requirements": user_requirements}
                    )

                    response_text += "\n\n" + GPTService.generate_product_response(
                        current_context,
                        selected_products
                    )
                    products = selected_products[:5]
                else:
                    response_text = f"""😔 Не удалось собрать ПК - отсутствуют товары категории **{fallback_category}**.

Свяжитесь с нами: +7 (777) 123-45-67"""

            else:
                # УСПЕШНАЯ СБОРКА
                logger.info("[PC_BUILD] All categories available, selecting components...")

                try:
                    selected_skus_by_category = GPTService.select_pc_components(
                        all_products_by_category,
                        user_requirements,
                        build_tier,
                        max_budget=budget,
                        include_peripherals=include_peripherals
                    )
                    logger.info(f"[PC_BUILD] GPT selected: {selected_skus_by_category}")
                except Exception as e:
                    logger.error(f"[PC_BUILD] GPT selection failed: {e}", exc_info=True)
                    selected_skus_by_category = {}

                # Валидируем выбранные SKU
                selected_build_details = {}
                validation_errors = []

                if len(selected_skus_by_category) == len(required_categories):
                    for category, sku in selected_skus_by_category.items():
                        product_detail = ProductSearchService.get_by_sku(sku)

                        if product_detail:
                            selected_build_details[category] = product_detail
                            logger.debug(f"[PC_BUILD] ✓ {category}: {product_detail.get('name')} "
                                        f"({product_detail.get('credit')} ₸)")
                        else:
                            validation_errors.append(f"{category} (SKU: {sku})")
                            logger.error(f"[PC_BUILD] ✗ {category}: SKU {sku} not found in API")

                # Генерируем ответ
                if len(selected_build_details) == len(required_categories):
                    # Все компоненты валидны
                    total_price = sum(
                        float(p.get('credit', 0))
                        for p in selected_build_details.values()
                    )
                    logger.info(f"[PC_BUILD] SUCCESS! Total price: {total_price:,.0f} ₸")

                    response_text = GPTService.generate_pc_build_response(
                        current_context,
                        selected_build_details
                    )
                    products = list(selected_build_details.values())

                else:
                    # Ошибка валидации
                    logger.error(f"[PC_BUILD] Validation failed: {validation_errors}")
                    response_text = f"""😔 Не удалось собрать совместимую конфигурацию.

**Проблема:** Некоторые компоненты недоступны: {', '.join(validation_errors) if validation_errors else 'неизвестная ошибка'}

Попробуйте изменить бюджет или требования, либо свяжитесь с консультантом."""

        
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

        # Детальное логирование результата
        log_event(
            session, 'bot_response', 'Ответ бота сгенерирован',
            user_input=user_message[:200] if user_message else '',
            bot_output=response_text[:500] if response_text else '',
            intent=intent,
            response_time_ms=response_time
        )

        # Структурированный лог для мониторинга
        logger.info(
            f"[CHAT_COMPLETE] "
            f"session={session.session_id[:8]}... | "
            f"intent={intent} | "
            f"products={len(products)} | "
            f"time={response_time}ms | "
            f"query_len={len(user_message) if user_message else 0}"
        )

        # Предупреждение о медленных запросах
        if response_time > 5000:
            logger.warning(f"[SLOW_REQUEST] {response_time}ms for intent={intent}")

        # Возвращаем ответ
        return JsonResponse({
            "success": True,
            "response": response_text,
            "products": products,
            "intent": intent,
            "session_id": session.session_id,
            "with_manager": session.status in ['pending_manager', 'with_manager']
        })

    except json.JSONDecodeError as e:
        logger.error(f"[CHAT_ERROR] Invalid JSON: {e}")
        return JsonResponse({
            "success": False,
            "error": "Неверный формат данных"
        }, status=400)

    except Exception as e:
        # Вычисляем время до ошибки
        error_time = int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0

        logger.error(
            f"[CHAT_ERROR] Exception in chat_assistant: {type(e).__name__}: {str(e)} | "
            f"time={error_time}ms",
            exc_info=True
        )

        # Логируем ошибку в БД если сессия существует
        if 'session' in locals() and session:
            log_event(
                session, 'error',
                f'Критическая ошибка: {type(e).__name__}',
                severity='error',
                error_details=str(e)[:500],
                response_time_ms=error_time
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