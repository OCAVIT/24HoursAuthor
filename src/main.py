"""FastAPI приложение — точка входа + APScheduler оркестратор."""

import asyncio
import logging
import signal
import time
from contextlib import asynccontextmanager
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse

from src.config import settings
from src.database.connection import async_session
from src.database.crud import (
    create_order,
    get_order_by_avtor24_id,
    update_order_status,
    get_orders_by_status,
    create_action_log,
    create_message,
    get_messages_for_order,
    track_api_usage,
    upsert_daily_stats,
    get_daily_stats,
)
from src.analyzer.price_calculator import estimate_income
from src.notifications.events import push_notification
from src.notifications.websocket import notification_manager, log_manager
from src.scraper.antiban import (
    is_banned, set_ban, clear_ban, get_ban_info,
    check_page_for_ban, check_daily_bid_limit, MAX_DAILY_BIDS,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

start_time = time.time()
scheduler = AsyncIOScheduler()

# Флаг работы бота (можно остановить через дашборд)
bot_running = True

# Флаг для graceful shutdown — текущие задачи завершаются, новые не запускаются
_shutting_down = False

# Счётчик активных задач (для ожидания завершения при shutdown)
_active_tasks = 0
_active_tasks_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

async def _track_task():
    """Инкрементировать счётчик активных задач."""
    global _active_tasks
    async with _active_tasks_lock:
        _active_tasks += 1


async def _untrack_task():
    """Декрементировать счётчик активных задач."""
    global _active_tasks
    async with _active_tasks_lock:
        _active_tasks = max(0, _active_tasks - 1)


async def _log_action(action: str, details: str = "", order_id: int | None = None) -> None:
    """Записать действие в БД и отправить в WebSocket логов."""
    try:
        async with async_session() as session:
            await create_action_log(session, action=action, details=details, order_id=order_id)
    except Exception as e:
        logger.error("Ошибка записи action_log: %s", e)

    await log_manager.broadcast({
        "action": action,
        "details": details,
        "order_id": order_id,
        "timestamp": datetime.now().isoformat(),
    })


async def _retry_async(coro_func, *args, max_retries: int = 3, **kwargs):
    """Повторить вызов async-функции с экспоненциальным backoff при сетевых ошибках."""
    for attempt in range(max_retries):
        try:
            return await coro_func(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt * 5  # 5, 10, 20 секунд
            logger.warning(
                "Сетевая ошибка (попытка %d/%d): %s. Повтор через %d сек.",
                attempt + 1, max_retries, e, wait,
            )
            await asyncio.sleep(wait)
    return None


# ---------------------------------------------------------------------------
# Задача 1: Сканирование заказов
# ---------------------------------------------------------------------------

async def scan_orders_job() -> None:
    """Сканировать новые заказы, оценить, поставить ставки."""
    if not bot_running or _shutting_down:
        return

    # Проверка бана — если бан активен, не сканируем
    if is_banned():
        await _log_action("antiban", f"Скан пропущен: бан активен, осталось {ban_remaining_seconds()} сек")
        return

    from src.scraper.auth import login
    from src.scraper.orders import fetch_order_list
    from src.scraper.order_detail import fetch_order_detail
    from src.scraper.bidder import place_bid
    from src.scraper.file_handler import download_files
    from src.scraper.browser import browser_manager
    from src.analyzer.order_scorer import score_order
    from src.analyzer.price_calculator import calculate_price
    from src.analyzer.file_analyzer import extract_all_content
    from src.analyzer.field_extractor import extract_missing_fields
    from src.generator.router import is_supported, is_banned as is_work_type_banned
    from src.ai_client import chat_completion
    from src.scraper.antiban import check_page_for_ban, ban_remaining_seconds

    await _track_task()
    try:
        page = await _retry_async(login)

        # Проверяем страницу на бан после логина
        if await check_page_for_ban(page):
            await _log_action("antiban", "Бан обнаружен после логина, пауза 30 мин")
            async with async_session() as session:
                await push_notification(
                    session,
                    type="error",
                    title="Обнаружена блокировка",
                    body={"error": get_ban_info()["reason"], "requires_attention": True},
                )
            return

        await _log_action("scan", "Начало сканирования заказов")

        # Проверяем дневной лимит ставок
        async with async_session() as session:
            today_stats = await get_daily_stats(session, date.today())
        bids_today = today_stats.bids_placed if today_stats else 0

        if not await check_daily_bid_limit(bids_today):
            await _log_action(
                "antiban",
                f"Дневной лимит ставок достигнут: {bids_today}/{MAX_DAILY_BIDS}",
            )
            return

        order_summaries = await _retry_async(fetch_order_list, page)
        if not order_summaries:
            await _log_action("scan", "Новых заказов не найдено")
            return

        await _log_action("scan", f"Найдено {len(order_summaries)} заказов")

        for summary in order_summaries:
            # Проверяем бан, shutdown и bot_running на каждой итерации
            if is_banned() or _shutting_down or not bot_running:
                break

            # Перепроверяем лимит после каждой ставки
            async with async_session() as session:
                today_stats = await get_daily_stats(session, date.today())
            bids_today = today_stats.bids_placed if today_stats else 0
            if not await check_daily_bid_limit(bids_today):
                await _log_action("antiban", f"Лимит ставок ({MAX_DAILY_BIDS}) достигнут в процессе сканирования")
                break

            try:
                # Дедупликация по БД
                async with async_session() as session:
                    existing = await get_order_by_avtor24_id(session, summary.order_id)
                if existing:
                    continue

                # Случайная задержка для антибана
                await browser_manager.random_delay(min_sec=2, max_sec=8)

                # Парсим детали заказа
                detail = await _retry_async(fetch_order_detail, page, summary.url)

                # Stop-gate: запрещённые типы работ
                if is_work_type_banned(detail.work_type):
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} — тип '{detail.work_type}' запрещён (stop-gate)",
                    )
                    continue

                # Проверяем поддерживается ли тип работы
                if not is_supported(detail.work_type):
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} — тип '{detail.work_type}' не поддерживается",
                    )
                    continue

                # Пропускаем заказы с договорной ценой (нет фиксированного бюджета)
                if not detail.budget_rub:
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} — договорная цена, пропускаем",
                    )
                    continue

                # Скачивание файлов заказа (если есть)
                downloaded_files = []
                files_text = ""
                if detail.file_urls:
                    try:
                        downloaded_files = await _retry_async(
                            download_files, page, detail.order_id, detail.file_urls,
                        )
                        if downloaded_files:
                            await _log_action(
                                "scan",
                                f"Заказ #{summary.order_id} — скачано {len(downloaded_files)} файлов",
                            )
                    except Exception as e:
                        logger.warning("Ошибка скачивания файлов для %s: %s", summary.order_id, e)

                # Извлечение контента из файлов (текст + vision для изображений)
                vision_cost = 0.0
                vision_in_tokens = 0
                vision_out_tokens = 0
                if downloaded_files:
                    try:
                        content_result = await extract_all_content(downloaded_files)
                        files_text = content_result.all_text
                        vision_cost = content_result.total_cost_usd
                        vision_in_tokens = content_result.total_input_tokens
                        vision_out_tokens = content_result.total_output_tokens
                        if content_result.vision_texts:
                            await _log_action(
                                "scan",
                                f"Заказ #{summary.order_id} — распознано {len(content_result.vision_texts)} изображений",
                            )
                    except Exception as e:
                        logger.warning("Ошибка извлечения контента для %s: %s", summary.order_id, e)

                # Извлечение недостающих полей из описания и файлов
                extraction_cost = 0.0
                extraction_in_tokens = 0
                extraction_out_tokens = 0
                try:
                    extraction_result = await extract_missing_fields(detail, files_text)
                    detail = extraction_result.order
                    extraction_cost = extraction_result.cost_usd
                    extraction_in_tokens = extraction_result.input_tokens
                    extraction_out_tokens = extraction_result.output_tokens
                    if extraction_result.fields_extracted:
                        await _log_action(
                            "scan",
                            f"Заказ #{summary.order_id} — извлечены поля: "
                            f"{', '.join(extraction_result.fields_extracted)}",
                        )
                except Exception as e:
                    logger.warning("Ошибка извлечения полей для %s: %s", summary.order_id, e)

                # Скоринг через AI (с полными данными)
                score_result = await _retry_async(score_order, detail)
                await _log_action(
                    "score",
                    f"Заказ #{summary.order_id} — score={score_result.score}, "
                    f"can_do={score_result.can_do}, reason={score_result.reason}",
                )

                # Сохраняем заказ в БД
                async with async_session() as session:
                    db_order = await create_order(
                        session,
                        avtor24_id=summary.order_id,
                        title=detail.title,
                        work_type=detail.work_type,
                        subject=detail.subject,
                        description=detail.description,
                        pages_min=detail.pages_min,
                        pages_max=detail.pages_max,
                        font_size=detail.font_size,
                        line_spacing=detail.line_spacing,
                        required_uniqueness=detail.required_uniqueness,
                        antiplagiat_system=detail.antiplagiat_system,
                        deadline=None,  # Строка deadline требует доп. парсинга
                        budget_rub=detail.budget_rub,
                        score=score_result.score,
                        status="scored",
                        customer_username=detail.customer_name[:100] if detail.customer_name else None,
                        formatting_requirements=detail.formatting_requirements or None,
                        structure=detail.structure or None,
                        special_requirements=detail.special_requirements or None,
                        extracted_from_files=detail.extracted_from_files,
                    )

                    # Трекинг API usage для скоринга
                    await track_api_usage(
                        session,
                        model=settings.openai_model_fast,
                        purpose="scoring",
                        input_tokens=score_result.input_tokens,
                        output_tokens=score_result.output_tokens,
                        cost_usd=score_result.cost_usd,
                        order_id=db_order.id,
                    )

                    # Трекинг API usage для vision (если были вызовы)
                    if vision_in_tokens > 0 or vision_out_tokens > 0:
                        await track_api_usage(
                            session,
                            model=settings.openai_model_main,
                            purpose="vision",
                            input_tokens=vision_in_tokens,
                            output_tokens=vision_out_tokens,
                            cost_usd=vision_cost,
                            order_id=db_order.id,
                        )

                    # Трекинг API usage для field extraction (если были вызовы)
                    if extraction_in_tokens > 0 or extraction_out_tokens > 0:
                        await track_api_usage(
                            session,
                            model=settings.openai_model_fast,
                            purpose="extraction",
                            input_tokens=extraction_in_tokens,
                            output_tokens=extraction_out_tokens,
                            cost_usd=extraction_cost,
                            order_id=db_order.id,
                        )

                # Решение о ставке
                if not score_result.can_do or score_result.score < 60:
                    async with async_session() as session:
                        await update_order_status(session, db_order.id, "rejected")
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} отклонён (score={score_result.score})",
                        order_id=db_order.id,
                    )
                    continue

                # Рассчитать цену
                bid_price = calculate_price(detail)

                # Сгенерировать комментарий к ставке
                bid_comment = ""
                try:
                    comment_result = await chat_completion(
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Ты автор на платформе Автор24. Напиши короткий "
                                    "комментарий к ставке (2-3 предложения). "
                                    "Упомяни опыт в теме, обещай сдачу вовремя."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Заказ: {detail.work_type} по предмету {detail.subject}. "
                                    f"Тема: {detail.title}"
                                ),
                            },
                        ],
                        model=settings.openai_model_fast,
                        temperature=0.8,
                        max_tokens=150,
                    )
                    bid_comment = comment_result["content"].strip()
                except Exception as e:
                    logger.warning("Ошибка генерации комментария к ставке: %s", e)
                    bid_comment = (
                        "Добрый день! Тема знакома, имею опыт в данной области. "
                        "Готов выполнить качественно и в срок."
                    )

                # Ставим ставку
                await browser_manager.random_delay(min_sec=5, max_sec=15)
                bid_ok = await _retry_async(place_bid, page, summary.url, bid_price, bid_comment)

                if bid_ok:
                    async with async_session() as session:
                        await update_order_status(
                            session,
                            db_order.id,
                            "bid_placed",
                            bid_price=bid_price,
                            bid_comment=bid_comment,
                            bid_placed_at=datetime.now(),
                        )

                        # Обновляем дневную статистику
                        today = date.today()
                        stats = await get_daily_stats(session, today)
                        bids_today = (stats.bids_placed if stats else 0) + 1
                        await upsert_daily_stats(session, today, bids_placed=bids_today)

                    await _log_action(
                        "bid",
                        f"Заказ #{summary.order_id} — ставка {bid_price}₽",
                        order_id=db_order.id,
                    )

                    # Уведомление на дашборд
                    async with async_session() as session:
                        await push_notification(
                            session,
                            type="new_order",
                            title=f"Ставка на: {detail.title[:60]}",
                            body={
                                "order_id": summary.order_id,
                                "title": detail.title,
                                "work_type": detail.work_type,
                                "budget": detail.budget,
                                "deadline": detail.deadline,
                                "score": score_result.score,
                                "bid_placed": True,
                                "bid_price": bid_price,
                            },
                            order_id=db_order.id,
                        )

                    # Комментарий к ставке уже служит приветствием.
                    # Дополнительное сообщение в чат НЕ отправляем — ждём одобрения.
                    # Уточняющие вопросы зададим после принятия (check_accepted_bids_job).

                else:
                    await _log_action(
                        "bid",
                        f"Заказ #{summary.order_id} — не удалось поставить ставку",
                        order_id=db_order.id,
                    )

            except Exception as e:
                logger.error("Ошибка обработки заказа %s: %s", summary.order_id, e)
                await _log_action("error", f"Ошибка обработки заказа #{summary.order_id}: {e}")

    except Exception as e:
        logger.error("Критическая ошибка в scan_orders_job: %s", e)
        await _log_action("error", f"Критическая ошибка сканирования: {e}")
        try:
            async with async_session() as session:
                await push_notification(
                    session,
                    type="error",
                    title="Ошибка сканирования заказов",
                    body={"error": str(e), "requires_attention": True},
                )
        except Exception:
            pass
    finally:
        await _untrack_task()


# ---------------------------------------------------------------------------
# Задача 2: Обработка принятых заказов
# ---------------------------------------------------------------------------

async def process_accepted_orders_job() -> None:
    """Обработать принятые заказы: генерация → антиплагиат → доставка."""
    if not bot_running or _shutting_down:
        return

    if is_banned():
        return

    from src.scraper.auth import login
    from src.scraper.chat import send_file_with_message
    from src.scraper.browser import browser_manager
    from src.generator.router import generate_and_check
    from src.docgen.builder import build_docx

    await _track_task()
    try:
        # Получаем заказы в статусе 'accepted'
        async with async_session() as session:
            accepted_orders = await get_orders_by_status(session, "accepted")

        if not accepted_orders:
            return

        await _log_action("generate", f"Найдено {len(accepted_orders)} принятых заказов для обработки")

        page = await _retry_async(login)

        for order in accepted_orders:
            if _shutting_down or not bot_running:
                break
            try:
                await _log_action(
                    "generate",
                    f"Начата генерация: {order.work_type}, ~{order.pages_max or order.pages_min or '?'} стр",
                    order_id=order.id,
                )

                # Уведомление о начале генерации
                async with async_session() as session:
                    await update_order_status(session, order.id, "generating")
                    await push_notification(
                        session,
                        type="order_accepted",
                        title=f"Генерация: {order.title[:60]}",
                        body={"order_id": order.avtor24_id, "title": order.title, "status": "generating"},
                        order_id=order.id,
                    )

                # Генерация + проверка антиплагиат
                gen_result, check_result = await generate_and_check(
                    work_type=order.work_type or "Эссе",
                    title=order.title,
                    description=order.description or "",
                    subject=order.subject or "",
                    pages=order.pages_max or order.pages_min,
                    required_uniqueness=order.required_uniqueness,
                    font_size=order.font_size or 14,
                    line_spacing=order.line_spacing or 1.5,
                    antiplagiat_system=order.antiplagiat_system or "textru",
                )

                if gen_result is None:
                    async with async_session() as session:
                        await update_order_status(
                            session, order.id, "error",
                            error_message="Генерация не удалась: тип не поддерживается",
                        )
                    await _log_action("error", "Генерация не удалась", order_id=order.id)
                    continue

                # Трекинг API usage
                async with async_session() as session:
                    await track_api_usage(
                        session,
                        model=settings.openai_model_main,
                        purpose="generation",
                        input_tokens=gen_result.input_tokens,
                        output_tokens=gen_result.output_tokens,
                        cost_usd=gen_result.cost_usd,
                        order_id=order.id,
                    )

                uniqueness = check_result.uniqueness if check_result else 0.0
                await _log_action(
                    "generate",
                    f"Завершено: ~{gen_result.pages_approx} стр, "
                    f"${gen_result.cost_usd:.2f}",
                    order_id=order.id,
                )

                # Обновляем статус на проверку антиплагиата
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "checking_plagiarism",
                        uniqueness_percent=uniqueness,
                        api_cost_usd=gen_result.cost_usd,
                        api_tokens_used=gen_result.total_tokens,
                    )

                await _log_action(
                    "plagiarism",
                    f"Уникальность: {uniqueness:.1f}%"
                    f" (требуется {order.required_uniqueness or settings.min_uniqueness}%)"
                    f" — {'OK' if (check_result and check_result.is_sufficient) else 'НЕДОСТАТОЧНО'}",
                    order_id=order.id,
                )

                # Сборка DOCX
                docx_path = await build_docx(
                    title=order.title,
                    text=gen_result.text,
                    work_type=order.work_type or "Реферат",
                    subject=order.subject or "",
                    font_size=order.font_size or 14,
                    line_spacing=order.line_spacing or 1.5,
                )

                if docx_path is None:
                    await _log_action("error", "Не удалось сгенерировать DOCX", order_id=order.id)
                    async with async_session() as session:
                        await update_order_status(
                            session, order.id, "error",
                            error_message="Не удалось собрать DOCX файл",
                        )
                    continue

                # Загрузка готовой работы заказчику
                await browser_manager.random_delay(min_sec=3, max_sec=8)
                delivery_message = (
                    "Добрый день! Работа готова, загружаю файл. "
                    "Если потребуются правки — пишите, исправлю."
                )

                send_ok = await _retry_async(
                    send_file_with_message, page, order.avtor24_id, str(docx_path), delivery_message,
                )

                if send_ok:
                    async with async_session() as session:
                        income = estimate_income(order.bid_price) if order.bid_price else 0
                        await update_order_status(
                            session, order.id, "delivered",
                            generated_file_path=str(docx_path),
                            income_rub=income,
                        )

                        # Сохраняем исходящее сообщение
                        await create_message(
                            session,
                            order_id=order.id,
                            direction="outgoing",
                            text=delivery_message,
                            is_auto_reply=True,
                        )

                        # Дневная статистика
                        today = date.today()
                        stats = await get_daily_stats(session, today)
                        await upsert_daily_stats(
                            session,
                            today,
                            orders_delivered=(stats.orders_delivered if stats else 0) + 1,
                            income_rub=(stats.income_rub if stats else 0) + income,
                            api_cost_usd=(stats.api_cost_usd if stats else 0) + gen_result.cost_usd,
                            api_tokens_used=(stats.api_tokens_used if stats else 0) + gen_result.total_tokens,
                        )

                        # Уведомление
                        await push_notification(
                            session,
                            type="order_delivered",
                            title=f"Отправлено: {order.title[:60]}",
                            body={
                                "order_id": order.avtor24_id,
                                "pages": gen_result.pages_approx,
                                "uniqueness": uniqueness,
                                "antiplagiat_system": order.antiplagiat_system or "textru",
                                "income": income,
                                "api_cost": gen_result.cost_usd,
                            },
                            order_id=order.id,
                        )

                    await _log_action("deliver", "Файл загружен в чат заказчика", order_id=order.id)
                    await _log_action(
                        "chat",
                        f"Отправлено: \"{delivery_message}\"",
                        order_id=order.id,
                    )
                else:
                    async with async_session() as session:
                        await update_order_status(
                            session, order.id, "error",
                            error_message="Не удалось отправить файл заказчику",
                            generated_file_path=str(docx_path),
                        )
                    await _log_action("error", "Не удалось отправить файл", order_id=order.id)

            except Exception as e:
                logger.error("Ошибка обработки заказа #%s: %s", order.avtor24_id, e)
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "error",
                        error_message=str(e)[:500],
                    )
                    await push_notification(
                        session,
                        type="error",
                        title=f"Ошибка: {order.title[:40]}",
                        body={
                            "order_id": order.avtor24_id,
                            "error": str(e)[:300],
                            "requires_attention": True,
                        },
                        order_id=order.id,
                    )
                await _log_action("error", f"Ошибка обработки: {e}", order_id=order.id)

    except Exception as e:
        logger.error("Критическая ошибка в process_accepted_orders_job: %s", e)
        await _log_action("error", f"Критическая ошибка обработки заказов: {e}")
    finally:
        await _untrack_task()


# ---------------------------------------------------------------------------
# Задача 3: Чат-респондер
# ---------------------------------------------------------------------------

async def chat_responder_job() -> None:
    """Проверить новые сообщения от заказчиков и ответить через AI."""
    if not bot_running or _shutting_down:
        return

    if is_banned():
        return

    from src.scraper.auth import login
    from src.scraper.chat import get_active_chats, get_messages, send_message, download_chat_files
    from src.scraper.browser import browser_manager
    from src.chat_ai.responder import generate_response, parse_customer_answer
    from src.database.crud import update_order_fields

    await _track_task()
    try:
        page = await _retry_async(login)

        active_chats = await _retry_async(get_active_chats, page)
        if not active_chats:
            return

        await _log_action("chat", f"Найдено {len(active_chats)} чатов с новыми сообщениями")

        for avtor24_id in active_chats:
            if _shutting_down or not bot_running:
                break
            try:
                # Ищем заказ в БД
                async with async_session() as session:
                    order = await get_order_by_avtor24_id(session, avtor24_id)
                if not order:
                    continue

                # Получаем историю сообщений
                await browser_manager.random_delay(min_sec=2, max_sec=5)
                chat_messages = await _retry_async(get_messages, page, avtor24_id)
                if not chat_messages:
                    continue

                # Последнее сообщение — от заказчика?
                last_msg = chat_messages[-1]
                if not last_msg.is_incoming:
                    continue  # Последнее — наше, ответ не нужен

                # Сохраняем входящее сообщение
                async with async_session() as session:
                    await create_message(
                        session,
                        order_id=order.id,
                        direction="incoming",
                        text=last_msg.text,
                    )

                # Скачиваем файлы из чата (если заказчик прикрепил)
                files_summary = ""
                if last_msg.has_files and last_msg.file_urls:
                    try:
                        downloaded_paths = await download_chat_files(
                            page, avtor24_id, last_msg.file_urls,
                        )
                        if downloaded_paths:
                            await _log_action(
                                "chat",
                                f"Скачано {len(downloaded_paths)} файлов из чата: "
                                f"{', '.join(p.split('/')[-1] for p in downloaded_paths)}",
                                order_id=order.id,
                            )
                            # Извлекаем содержимое для контекста
                            try:
                                from src.analyzer.file_analyzer import extract_all_content
                                from pathlib import Path
                                content = await extract_all_content(
                                    [Path(p) for p in downloaded_paths]
                                )
                                if content and content.get("all_text"):
                                    files_summary = content["all_text"][:2000]
                            except Exception as e:
                                logger.warning("Ошибка извлечения контента из файлов чата: %s", e)
                    except Exception as e:
                        logger.warning("Ошибка скачивания файлов из чата %s: %s", avtor24_id, e)

                # Парсинг ответа заказчика: обновляем поля если чего-то не хватает
                if not order.antiplagiat_system or not order.required_uniqueness:
                    try:
                        context_str = (
                            f"Тип: {order.work_type}, Предмет: {order.subject}, "
                            f"Тема: {order.title}"
                        )
                        parsed = await parse_customer_answer(
                            customer_text=last_msg.text,
                            order_context=context_str,
                        )
                        update_kwargs = {}
                        if parsed.get("antiplagiat_system") and not order.antiplagiat_system:
                            update_kwargs["antiplagiat_system"] = parsed["antiplagiat_system"]
                        if parsed.get("required_uniqueness") and not order.required_uniqueness:
                            update_kwargs["required_uniqueness"] = int(parsed["required_uniqueness"])
                        if update_kwargs:
                            async with async_session() as session:
                                await update_order_fields(session, order.id, **update_kwargs)
                            # Перечитаем заказ с обновлёнными полями
                            async with async_session() as session:
                                order = await get_order_by_avtor24_id(session, avtor24_id)
                            await _log_action(
                                "chat",
                                f"Обновлены поля из ответа заказчика: {update_kwargs}",
                                order_id=order.id,
                            )
                    except Exception as e:
                        logger.warning("Ошибка парсинга ответа заказчика: %s", e)

                # Формируем историю для AI
                message_history = []
                for msg in chat_messages[:-1]:  # Все кроме последнего
                    role = "user" if msg.is_incoming else "assistant"
                    message_history.append({"role": role, "content": msg.text})

                # Генерируем ответ с ПОЛНЫМ контекстом
                ai_response = await generate_response(
                    order_description=order.description or order.title,
                    message_history=message_history,
                    new_message=last_msg.text,
                    order_status=order.status or "",
                    work_type=order.work_type or "",
                    subject=order.subject or "",
                    deadline=str(order.deadline) if order.deadline else "",
                    required_uniqueness=order.required_uniqueness,
                    antiplagiat_system=order.antiplagiat_system or "",
                    bid_price=order.bid_price,
                    pages_min=order.pages_min,
                    pages_max=order.pages_max,
                    font_size=order.font_size or 14,
                    line_spacing=order.line_spacing or 1.5,
                    formatting_requirements=order.formatting_requirements or "",
                    structure=order.structure or "",
                    special_requirements=order.special_requirements or "",
                    files_summary=files_summary,
                )

                # Отправляем ответ
                await browser_manager.random_delay(min_sec=3, max_sec=10)
                send_ok = await _retry_async(send_message, page, avtor24_id, ai_response.text)

                if send_ok:
                    # Сохраняем исходящее сообщение
                    async with async_session() as session:
                        await create_message(
                            session,
                            order_id=order.id,
                            direction="outgoing",
                            text=ai_response.text,
                            is_auto_reply=True,
                        )

                        # Трекинг API usage
                        await track_api_usage(
                            session,
                            model=settings.openai_model_fast,
                            purpose="chat",
                            input_tokens=ai_response.input_tokens,
                            output_tokens=ai_response.output_tokens,
                            cost_usd=ai_response.cost_usd,
                            order_id=order.id,
                        )

                        # Уведомление
                        await push_notification(
                            session,
                            type="new_message",
                            title=f"Сообщение: заказ #{avtor24_id}",
                            body={
                                "order_id": avtor24_id,
                                "customer_message": last_msg.text[:200],
                                "auto_reply": ai_response.text,
                                "auto_replied": True,
                            },
                            order_id=order.id,
                        )

                    await _log_action(
                        "chat",
                        f"Авто-ответ заказчику #{avtor24_id}: \"{ai_response.text[:100]}\"",
                        order_id=order.id,
                    )
                else:
                    await _log_action(
                        "error",
                        f"Не удалось отправить ответ в чат #{avtor24_id}",
                        order_id=order.id,
                    )

            except Exception as e:
                logger.error("Ошибка в чат-респондере для заказа %s: %s", avtor24_id, e)
                await _log_action("error", f"Ошибка чат-респондера для #{avtor24_id}: {e}")

    except Exception as e:
        logger.error("Критическая ошибка в chat_responder_job: %s", e)
        await _log_action("error", f"Критическая ошибка чат-респондера: {e}")
    finally:
        await _untrack_task()


# ---------------------------------------------------------------------------
# Задача 4: Ежедневная сводка (22:00)
# ---------------------------------------------------------------------------

async def daily_summary_job() -> None:
    """Сформировать и отправить ежедневную сводку."""
    try:
        today = date.today()

        async with async_session() as session:
            stats = await get_daily_stats(session, today)

            body = {
                "bids_placed": stats.bids_placed if stats else 0,
                "orders_accepted": stats.orders_accepted if stats else 0,
                "orders_delivered": stats.orders_delivered if stats else 0,
                "income_today": stats.income_rub if stats else 0,
                "api_cost_today": stats.api_cost_usd if stats else 0,
                "api_tokens_today": stats.api_tokens_used if stats else 0,
            }

            await push_notification(
                session,
                type="daily_summary",
                title=f"Сводка за {today.strftime('%d.%m.%Y')}",
                body=body,
            )

        await _log_action("system", f"Ежедневная сводка за {today}: {body}")
        logger.info("Ежедневная сводка отправлена: %s", body)

    except Exception as e:
        logger.error("Ошибка формирования ежедневной сводки: %s", e)
        await _log_action("error", f"Ошибка ежедневной сводки: {e}")


# ---------------------------------------------------------------------------
# Проверка принятых заказов на стороне Автор24
# ---------------------------------------------------------------------------

async def check_accepted_bids_job() -> None:
    """Проверить, приняли ли заказчики наши ставки (перевод bid_placed → accepted)."""
    if not bot_running or _shutting_down:
        return

    if is_banned():
        return

    from src.scraper.auth import login
    from src.scraper.browser import browser_manager

    await _track_task()
    try:
        page = await _retry_async(login)

        # Переходим на страницу наших заказов
        my_orders_url = f"{settings.avtor24_base_url}/cabinet/orders"
        await page.goto(my_orders_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.short_delay()

        # Ищем заказы в статусе "В работе" / "Оплачен"
        active_items = await page.locator(
            "[data-order-id], .order-item, .my-order-card"
        ).all()

        for item in active_items:
            if _shutting_down or not bot_running:
                break
            try:
                import re
                # Извлекаем order_id
                oid = await item.get_attribute("data-order-id")
                if not oid:
                    link = item.locator("a[href*='/order/']")
                    if await link.count() > 0:
                        href = await link.first.get_attribute("href")
                        match = re.search(r"/order/(\d+)", href or "")
                        oid = match.group(1) if match else None

                if not oid:
                    continue

                # Проверяем в БД — если bid_placed, значит ещё не обработан
                async with async_session() as session:
                    order = await get_order_by_avtor24_id(session, oid)
                if not order or order.status != "bid_placed":
                    continue

                # Проверяем статус на странице (есть ли "В работе", "Оплачен", и т.д.)
                status_el = item.locator(".status, .order-status, .badge")
                if await status_el.count() > 0:
                    status_text = (await status_el.first.inner_text()).strip().lower()
                    if any(kw in status_text for kw in ["в работе", "оплачен", "принят", "выполняется"]):
                        async with async_session() as session:
                            await update_order_status(session, order.id, "accepted")

                            today = date.today()
                            stats = await get_daily_stats(session, today)
                            await upsert_daily_stats(
                                session, today,
                                orders_accepted=(stats.orders_accepted if stats else 0) + 1,
                            )

                            await push_notification(
                                session,
                                type="order_accepted",
                                title=f"Принят: {order.title[:60]}",
                                body={"order_id": oid, "title": order.title, "status": "accepted"},
                                order_id=order.id,
                            )

                        await _log_action("accept", f"Заказ #{oid} принят заказчиком", order_id=order.id)

                        # Отправляем уточняющее сообщение (если не хватает данных)
                        try:
                            from src.chat_ai.responder import generate_clarifying_message
                            from src.scraper.chat import send_message as chat_send_message

                            await browser_manager.random_delay(min_sec=3, max_sec=8)
                            clarify_msg = await generate_clarifying_message(
                                work_type=order.work_type or "",
                                subject=order.subject or "",
                                title=order.title or "",
                                description=order.description or "",
                                required_uniqueness=order.required_uniqueness,
                                antiplagiat_system=order.antiplagiat_system or "",
                                bid_price=order.bid_price or 0,
                            )
                            if clarify_msg:
                                send_ok = await chat_send_message(page, oid, clarify_msg.text)
                                if send_ok:
                                    async with async_session() as session:
                                        await create_message(
                                            session,
                                            order_id=order.id,
                                            direction="outgoing",
                                            text=clarify_msg.text,
                                            is_auto_reply=True,
                                        )
                                    await _log_action(
                                        "chat",
                                        f"Уточняющее сообщение отправлено: \"{clarify_msg.text[:100]}\"",
                                        order_id=order.id,
                                    )
                        except Exception as e:
                            logger.warning("Ошибка отправки уточняющего сообщения: %s", e)

            except Exception as e:
                logger.warning("Ошибка проверки статуса заказа: %s", e)
                continue

    except Exception as e:
        logger.error("Ошибка проверки принятых заказов: %s", e)
        await _log_action("error", f"Ошибка проверки принятых ставок: {e}")
    finally:
        await _untrack_task()


# ---------------------------------------------------------------------------
# FastAPI Lifespan: запуск и остановка планировщика
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения — запуск APScheduler + graceful shutdown."""
    global _shutting_down

    # Запуск планировщика
    scheduler.add_job(
        scan_orders_job,
        trigger=IntervalTrigger(seconds=settings.scan_interval_seconds),
        id="scan_orders",
        name="Сканирование заказов",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        check_accepted_bids_job,
        trigger=IntervalTrigger(seconds=120),
        id="check_accepted",
        name="Проверка принятых ставок",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        process_accepted_orders_job,
        trigger=IntervalTrigger(seconds=120),
        id="process_accepted",
        name="Обработка принятых заказов",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        chat_responder_job,
        trigger=IntervalTrigger(seconds=120),
        id="chat_responder",
        name="Чат-респондер",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        daily_summary_job,
        trigger=CronTrigger(hour=22, minute=0),
        id="daily_summary",
        name="Ежедневная сводка",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info("APScheduler запущен с %d задачами", len(scheduler.get_jobs()))
    await _log_action("system", "Бот запущен, планировщик активирован")

    yield

    # Graceful shutdown: сигнализируем задачам о завершении
    _shutting_down = True
    logger.info("Начинается graceful shutdown...")
    await _log_action("system", "Graceful shutdown: ожидание завершения текущих задач")

    # Останавливаем планировщик (новые задачи не запустятся)
    scheduler.shutdown(wait=False)

    # Ждём завершения текущих задач (до 60 секунд)
    shutdown_deadline = time.time() + 60
    while _active_tasks > 0 and time.time() < shutdown_deadline:
        logger.info("Ожидание завершения %d задач...", _active_tasks)
        await asyncio.sleep(2)

    if _active_tasks > 0:
        logger.warning("Принудительная остановка: %d задач не завершились за 60 сек", _active_tasks)
    else:
        logger.info("Все задачи завершены корректно")

    # Закрываем браузер
    try:
        from src.scraper.browser import browser_manager
        await browser_manager.close()
    except Exception:
        pass

    logger.info("Бот остановлен")
    await _log_action("system", "Бот остановлен")


app = FastAPI(title="Avtor24 Bot", lifespan=lifespan)

# Подключаем роутер дашборда
from src.dashboard.app import router as dashboard_router
app.include_router(dashboard_router)


# ---------------------------------------------------------------------------
# HTTP и WebSocket эндпоинты
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Редирект на дашборд."""
    return RedirectResponse(url="/dashboard/")


@app.get("/health")
async def health():
    """Healthcheck эндпоинт."""
    uptime = int(time.time() - start_time)
    jobs = len(scheduler.get_jobs()) if scheduler.running else 0
    return {
        "status": "ok",
        "uptime": uptime,
        "bot_running": bot_running,
        "scheduler_jobs": jobs,
        "ban_info": get_ban_info(),
        "active_tasks": _active_tasks,
        "shutting_down": _shutting_down,
    }


@app.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket):
    """WebSocket для реалтайм уведомлений."""
    await notification_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect(websocket)


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """WebSocket для реалтайм логов."""
    await log_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_manager.disconnect(websocket)
