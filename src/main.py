"""FastAPI приложение — точка входа + APScheduler оркестратор."""

import asyncio
import logging
import random
import signal
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

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

# In-memory кеш уже обработанных order_id — пропускаем без обращения к БД.
# Сбрасывается при перезапуске; БД-дедупликация остаётся как fallback.
_seen_order_ids: set[str] = set()

# Кеш текстов ассистент-сообщений, которые уже обработаны: {order_id: set(text_hash)}
# Предотвращает повторный fetch_order_detail для одних и тех же уведомлений
_processed_assistant_msgs: dict[str, set[str]] = {}

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


async def _ensure_order_in_db(page, avtor24_id: str, status: str = "accepted"):
    """Убедиться что заказ есть в БД. Если нет — спарсить детали и создать запись.

    Используется в chat_responder_job и check_accepted_bids_job, когда
    заказ обнаружен в «Активных» на /home, но записи в БД нет
    (например, ставку поставили вручную на сайте).

    Returns:
        Order | None — запись из БД (найденная или только что созданная).
    """
    from src.scraper.order_detail import fetch_order_detail
    from src.scraper.browser import browser_manager

    async with async_session() as session:
        order = await get_order_by_avtor24_id(session, avtor24_id)
    if order:
        return order

    # Заказа нет — парсим детальную страницу и создаём запись
    try:
        detail_url = f"/order/getoneorder/{avtor24_id}"
        await browser_manager.random_delay(min_sec=2, max_sec=5)
        detail = await _retry_async(fetch_order_detail, page, detail_url)
        if detail is None:
            logger.warning("Не удалось спарсить детали заказа %s", avtor24_id)
            return None

        async with async_session() as session:
            order = await create_order(
                session,
                avtor24_id=avtor24_id,
                title=detail.title or f"Заказ #{avtor24_id}",
                work_type=detail.work_type or None,
                subject=detail.subject or None,
                description=detail.description or None,
                pages_min=detail.pages_min,
                pages_max=detail.pages_max,
                font_size=detail.font_size or 14,
                line_spacing=detail.line_spacing or 1.5,
                required_uniqueness=detail.required_uniqueness,
                antiplagiat_system=detail.antiplagiat_system or None,
                budget_rub=detail.budget_rub,
                customer_username=detail.customer_name or None,
                status=status,
            )
        await _log_action(
            "accept",
            f"Заказ #{avtor24_id} обнаружен в «Активных», но не был в БД — создан со статусом '{status}'",
            order_id=order.id,
        )
        return order
    except Exception as e:
        logger.warning("Ошибка создания заказа %s из активных: %s", avtor24_id, e)
        await _log_action("error", f"Не удалось создать запись для заказа #{avtor24_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Реалистичная задержка перед доставкой (имитация работы человека)
# ---------------------------------------------------------------------------

# Базовое время «работы» в минутах по типу работы
_DELIVERY_BASE_MIN: dict[str, int] = {
    "Эссе": 20,
    "Сочинение": 20,
    "Реферат": 40,
    "Доклад": 30,
    "Курсовая работа": 90,
    "Выпускная квалификационная работа (ВКР)": 240,
    "Дипломная работа": 240,
    "Контрольная работа": 30,
    "Решение задач": 25,
    "Ответы на вопросы": 20,
    "Презентации": 30,
    "Перевод": 25,
    "Бизнес-план": 90,
    "Отчёт по практике": 60,
    "Научно-исследовательская работа (НИР)": 120,
    "Статья": 40,
}
_DELIVERY_PER_PAGE = 3       # минут на страницу
_DELIVERY_MIN_TOTAL = 15     # минимальная задержка
_DELIVERY_MAX_TOTAL = 480    # максимум 8 часов


def _calculate_delivery_delay(work_type: str | None, pages: int | None) -> int:
    """Рассчитать реалистичную задержку (в минутах) перед отправкой работы.

    Формула: base[work_type] + pages × 3 мин, ±20% рандом.
    """
    base = _DELIVERY_BASE_MIN.get(work_type or "", 30)
    page_count = pages or 10
    total = base + page_count * _DELIVERY_PER_PAGE
    randomized = total * random.uniform(0.8, 1.2)
    return max(_DELIVERY_MIN_TOTAL, min(_DELIVERY_MAX_TOTAL, int(randomized)))


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
    _page_locked = False
    try:
        page = await _retry_async(login)
        await browser_manager.page_lock.acquire()
        _page_locked = True

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
                # Быстрая in-memory дедупликация (без обращения к БД)
                if summary.order_id in _seen_order_ids:
                    continue

                # Дедупликация по БД (fallback после перезапуска)
                async with async_session() as session:
                    existing = await get_order_by_avtor24_id(session, summary.order_id)
                if existing:
                    _seen_order_ids.add(summary.order_id)
                    continue

                # Случайная задержка для антибана
                await browser_manager.random_delay(min_sec=2, max_sec=8)

                # Парсим детали заказа
                detail = await _retry_async(fetch_order_detail, page, summary.url)

                # Stop-gate: запрещённые типы работ
                if is_work_type_banned(detail.work_type):
                    _seen_order_ids.add(summary.order_id)
                    # Сохраняем в БД чтобы не тратить ресурсы после перезапуска
                    async with async_session() as session:
                        await create_order(
                            session,
                            avtor24_id=summary.order_id,
                            title=detail.title or summary.title,
                            work_type=detail.work_type,
                            status="skipped",
                        )
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} — тип '{detail.work_type}' запрещён (stop-gate)",
                    )
                    continue

                # Проверяем поддерживается ли тип работы
                if not is_supported(detail.work_type):
                    _seen_order_ids.add(summary.order_id)
                    async with async_session() as session:
                        await create_order(
                            session,
                            avtor24_id=summary.order_id,
                            title=detail.title or summary.title,
                            work_type=detail.work_type,
                            status="skipped",
                        )
                    await _log_action(
                        "score",
                        f"Заказ #{summary.order_id} — тип '{detail.work_type}' не поддерживается",
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

                # Заказ проанализирован и сохранён — запоминаем
                _seen_order_ids.add(summary.order_id)

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
                # Запоминаем даже при ошибке — AI-токены уже потрачены,
                # повторный анализ их не вернёт.
                _seen_order_ids.add(summary.order_id)
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
        if _page_locked:
            browser_manager.page_lock.release()
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
    from src.scraper.order_detail import fetch_order_detail
    from src.generator.router import generate_and_check
    from src.docgen.builder import build_docx
    from src.database.crud import update_order_fields

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
                # === Перепарсинг страницы заказа (актуальные данные) ===
                # Заказчик мог изменить условия через Ассистента —
                # обязательно перечитываем страницу перед генерацией.
                try:
                    detail_url = f"/order/getoneorder/{order.avtor24_id}"
                    async with browser_manager.page_lock:
                        detail = await _retry_async(fetch_order_detail, page, detail_url)
                    if detail:
                        upd = {}
                        if detail.title and detail.title != order.title:
                            upd["title"] = detail.title
                        if detail.work_type and detail.work_type != (order.work_type or ""):
                            upd["work_type"] = detail.work_type
                        if detail.subject and detail.subject != (order.subject or ""):
                            upd["subject"] = detail.subject
                        if detail.description and detail.description != (order.description or ""):
                            upd["description"] = detail.description
                        if detail.pages_min and detail.pages_min != order.pages_min:
                            upd["pages_min"] = detail.pages_min
                        if detail.pages_max and detail.pages_max != order.pages_max:
                            upd["pages_max"] = detail.pages_max
                        if detail.required_uniqueness and detail.required_uniqueness != order.required_uniqueness:
                            upd["required_uniqueness"] = detail.required_uniqueness
                        if detail.antiplagiat_system and detail.antiplagiat_system != (order.antiplagiat_system or ""):
                            upd["antiplagiat_system"] = detail.antiplagiat_system
                        if detail.font_size and detail.font_size != (order.font_size or 14):
                            upd["font_size"] = detail.font_size
                        if detail.line_spacing and detail.line_spacing != (order.line_spacing or 1.5):
                            upd["line_spacing"] = detail.line_spacing
                        if detail.budget_rub and detail.budget_rub != order.budget_rub:
                            upd["budget_rub"] = detail.budget_rub
                        if upd:
                            async with async_session() as session:
                                await update_order_fields(session, order.id, **upd)
                            changes = ", ".join(f"{k}={v}" for k, v in upd.items())
                            await _log_action(
                                "generate",
                                f"Заказ #{order.avtor24_id}: условия обновлены перед генерацией: {changes}",
                                order_id=order.id,
                            )
                            # Перечитываем заказ с актуальными полями
                            async with async_session() as session:
                                order = await get_order_by_avtor24_id(session, order.avtor24_id)
                except Exception as e:
                    logger.warning("Ошибка перепарсинга заказа %s перед генерацией: %s", order.avtor24_id, e)

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

                # === Ставим в очередь на доставку с реалистичной задержкой ===
                delay_min = _calculate_delivery_delay(
                    order.work_type, order.pages_max or order.pages_min,
                )
                deliver_after = datetime.now() + timedelta(minutes=delay_min)
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "ready",
                        generated_file_path=str(docx_path),
                        uniqueness_percent=uniqueness,
                        api_cost_usd=gen_result.cost_usd,
                        api_tokens_used=gen_result.total_tokens,
                        # Храним время доставки в error_message (ISO формат)
                        error_message=deliver_after.isoformat(),
                    )
                await _log_action(
                    "generate",
                    f"Работа готова, доставка запланирована через ~{delay_min} мин "
                    f"(в {deliver_after.strftime('%H:%M')})",
                    order_id=order.id,
                )

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

        # ===================================================================
        # Этап 2: Доставка готовых работ (статус "ready", время пришло)
        # ===================================================================
        async with async_session() as session:
            ready_orders = await get_orders_by_status(session, "ready")

        for order in ready_orders:
            if _shutting_down or not bot_running:
                break
            try:
                # Проверяем: наступило ли время доставки?
                deliver_after_str = order.error_message or ""
                if deliver_after_str:
                    try:
                        deliver_after = datetime.fromisoformat(deliver_after_str)
                        if datetime.now() < deliver_after:
                            remaining = (deliver_after - datetime.now()).total_seconds() / 60
                            logger.debug(
                                "Заказ %s: доставка через ~%.0f мин",
                                order.avtor24_id, remaining,
                            )
                            continue  # Ещё не время
                    except (ValueError, TypeError):
                        pass  # Некорректная дата — доставляем сразу

                docx_path = order.generated_file_path
                if not docx_path:
                    await _log_action("error", "Нет файла для доставки", order_id=order.id)
                    async with async_session() as session:
                        await update_order_status(session, order.id, "error",
                                                  error_message="Файл для доставки не найден")
                    continue

                # Доставляем файл заказчику
                await browser_manager.random_delay(min_sec=3, max_sec=8)
                delivery_message = (
                    "Добрый день! Работа готова, загружаю файл. "
                    "Если потребуются правки — пишите, исправлю."
                )

                async with browser_manager.page_lock:
                    send_ok = await _retry_async(
                        send_file_with_message, page, order.avtor24_id,
                        str(docx_path), delivery_message,
                    )

                if send_ok:
                    async with async_session() as session:
                        income = estimate_income(order.bid_price) if order.bid_price else 0
                        await update_order_status(
                            session, order.id, "delivered",
                            income_rub=income,
                            error_message=None,  # Очищаем — там было время доставки
                        )

                        await create_message(
                            session,
                            order_id=order.id,
                            direction="outgoing",
                            text=delivery_message,
                            is_auto_reply=True,
                        )

                        today = date.today()
                        stats = await get_daily_stats(session, today)
                        await upsert_daily_stats(
                            session,
                            today,
                            orders_delivered=(stats.orders_delivered if stats else 0) + 1,
                            income_rub=(stats.income_rub if stats else 0) + income,
                            api_cost_usd=(stats.api_cost_usd if stats else 0) + (order.api_cost_usd or 0),
                            api_tokens_used=(stats.api_tokens_used if stats else 0) + (order.api_tokens_used or 0),
                        )

                        await push_notification(
                            session,
                            type="order_delivered",
                            title=f"Отправлено: {order.title[:60]}",
                            body={
                                "order_id": order.avtor24_id,
                                "uniqueness": order.uniqueness_percent or 0,
                                "antiplagiat_system": order.antiplagiat_system or "textru",
                                "income": income,
                                "api_cost": order.api_cost_usd or 0,
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
                        )
                    await _log_action("error", "Не удалось отправить файл", order_id=order.id)

            except Exception as e:
                logger.error("Ошибка доставки заказа #%s: %s", order.avtor24_id, e)
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "error",
                        error_message=f"Ошибка доставки: {str(e)[:400]}",
                    )
                await _log_action("error", f"Ошибка доставки: {e}", order_id=order.id)

    except Exception as e:
        logger.error("Критическая ошибка в process_accepted_orders_job: %s", e)
        await _log_action("error", f"Критическая ошибка обработки заказов: {e}")
    finally:
        await _untrack_task()


# ---------------------------------------------------------------------------
# Обработка сообщений Ассистента (изменение условий заказа)
# ---------------------------------------------------------------------------

async def _handle_assistant_messages(
    page,
    avtor24_id: str,
    order,
    assistant_msgs: list,
) -> None:
    """Обработать сообщения Ассистента: перепарсить условия заказа с детальной страницы.

    Ассистент на Автор24 отправляет сообщения об изменении условий заказа.
    Мы заходим на страницу заказа, парсим новые условия и обновляем БД.
    Если условия неприемлемые — можно отменить заказ (кнопка "Отменить").
    """
    from src.scraper.order_detail import fetch_order_detail
    from src.scraper.browser import browser_manager
    from src.scraper.chat import cancel_order
    from src.database.crud import update_order_fields
    from src.analyzer.price_calculator import is_profitable

    try:
        await _log_action(
            "chat",
            f"Обнаружено {len(assistant_msgs)} сообщений Ассистента, перепарсинг условий",
            order_id=order.id,
        )

        # Переходим на страницу заказа и парсим актуальные условия
        order_url = f"/order/getoneorder/{avtor24_id}"
        detail = await _retry_async(fetch_order_detail, page, order_url)

        # Собираем поля для обновления (только изменившиеся)
        update_kwargs = {}
        if detail.title and detail.title != order.title:
            update_kwargs["title"] = detail.title
        if detail.work_type and detail.work_type != (order.work_type or ""):
            update_kwargs["work_type"] = detail.work_type
        if detail.subject and detail.subject != (order.subject or ""):
            update_kwargs["subject"] = detail.subject
        if detail.description and detail.description != (order.description or ""):
            update_kwargs["description"] = detail.description
        if detail.required_uniqueness and detail.required_uniqueness != order.required_uniqueness:
            update_kwargs["required_uniqueness"] = detail.required_uniqueness
        if detail.antiplagiat_system and detail.antiplagiat_system != (order.antiplagiat_system or ""):
            update_kwargs["antiplagiat_system"] = detail.antiplagiat_system
        if detail.pages_min and detail.pages_min != order.pages_min:
            update_kwargs["pages_min"] = detail.pages_min
        if detail.pages_max and detail.pages_max != order.pages_max:
            update_kwargs["pages_max"] = detail.pages_max
        if detail.font_size and detail.font_size != (order.font_size or 14):
            update_kwargs["font_size"] = detail.font_size
        if detail.line_spacing and detail.line_spacing != (order.line_spacing or 1.5):
            update_kwargs["line_spacing"] = detail.line_spacing
        if detail.budget_rub and detail.budget_rub != order.budget_rub:
            update_kwargs["budget_rub"] = detail.budget_rub
        if detail.deadline and detail.deadline != (str(order.deadline) if order.deadline else None):
            update_kwargs["deadline"] = detail.deadline

        if update_kwargs:
            async with async_session() as session:
                await update_order_fields(session, order.id, **update_kwargs)
            changes_str = ", ".join(f"{k}={v}" for k, v in update_kwargs.items())
            await _log_action(
                "chat",
                f"Условия заказа #{avtor24_id} обновлены: {changes_str}",
                order_id=order.id,
            )

            async with async_session() as session:
                await push_notification(
                    session,
                    type="new_message",
                    title=f"Условия изменены: заказ #{avtor24_id}",
                    body={
                        "order_id": avtor24_id,
                        "customer_message": f"Ассистент: условия заказа изменены ({changes_str})",
                        "auto_reply": "",
                        "auto_replied": False,
                    },
                    order_id=order.id,
                )

            # --- Проверка прибыльности после изменения условий ---
            # Используем обновлённый work_type из detail (мог измениться)
            bid_price = order.bid_price
            new_work_type = update_kwargs.get("work_type", order.work_type) or "Другое"
            if bid_price and not is_profitable(bid_price, new_work_type):
                await _log_action(
                    "chat",
                    f"Заказ #{avtor24_id} стал нерентабельным после изменения условий "
                    f"(bid={bid_price}, work_type={new_work_type}), отменяем",
                    order_id=order.id,
                )
                cancelled = await cancel_order(page, avtor24_id)
                if cancelled:
                    async with async_session() as session:
                        await update_order_status(session, order.id, "cancelled")
                    async with async_session() as session:
                        await push_notification(
                            session,
                            type="error",
                            title=f"Заказ #{avtor24_id} отменён (нерентабельно)",
                            body={
                                "order_id": avtor24_id,
                                "error": (
                                    f"Условия изменены Ассистентом ({changes_str}), "
                                    f"заказ стал нерентабельным — автоотмена"
                                ),
                                "requires_attention": False,
                            },
                            order_id=order.id,
                        )
                    await _log_action(
                        "cancel",
                        f"Заказ #{avtor24_id} автоотменён: нерентабельно после изменения условий",
                        order_id=order.id,
                    )
                else:
                    await _log_action(
                        "error",
                        f"Не удалось отменить нерентабельный заказ #{avtor24_id} (кнопка не найдена?)",
                        order_id=order.id,
                    )
            else:
                # --- Пере-генерация если условия изменились на уже обработанном заказе ---
                # Если заказ уже готов/доставлен, но условия существенно изменились,
                # сбрасываем в "accepted" чтобы запустить повторную генерацию.
                regen_statuses = ("delivered", "ready", "generating", "checking_plagiarism", "rewriting", "error")
                significant_fields = {
                    "title", "description", "work_type", "subject",
                    "pages_min", "pages_max", "required_uniqueness",
                }
                has_significant = bool(significant_fields & set(update_kwargs.keys()))
                if order.status in regen_statuses and has_significant:
                    async with async_session() as session:
                        await update_order_status(
                            session, order.id, "accepted",
                            error_message=None,
                        )
                    await _log_action(
                        "generate",
                        f"Заказ #{avtor24_id} сброшен '{order.status}' → 'accepted' "
                        f"(условия изменены Ассистентом: {changes_str}), перегенерация",
                        order_id=order.id,
                    )
                    async with async_session() as session:
                        await push_notification(
                            session,
                            type="new_message",
                            title=f"Перегенерация: заказ #{avtor24_id}",
                            body={
                                "order_id": avtor24_id,
                                "customer_message": f"Условия изменены ({changes_str}), работа будет перегенерирована",
                                "auto_reply": "",
                                "auto_replied": False,
                            },
                            order_id=order.id,
                        )
        else:
            await _log_action(
                "chat",
                f"Сообщение Ассистента по заказу #{avtor24_id}, условия не изменились",
                order_id=order.id,
            )

    except Exception as e:
        logger.warning("Ошибка обработки сообщений Ассистента для %s: %s", avtor24_id, e)
        await _log_action(
            "error",
            f"Ошибка обработки Ассистента для #{avtor24_id}: {e}",
            order_id=order.id,
        )


# ---------------------------------------------------------------------------
# Проактивное сообщение (бот пишет первым)
# ---------------------------------------------------------------------------

# Минимум секунд с момента принятия заказа, прежде чем бот пишет первым
PROACTIVE_MSG_DELAY_SEC = 5 * 60  # 5 минут


async def _maybe_send_proactive_message(
    page,
    avtor24_id: str,
    order,
    chat_messages: list,
    browser_manager,
    send_message_fn,
    generate_proactive_fn,
) -> None:
    """Отправить проактивное сообщение, если заказчик молчит после принятия.

    Условия:
    1. Статус заказа = accepted (ещё не начали генерацию)
    2. Нет наших исходящих сообщений в БД (не писали ещё)
    3. Прошло >= PROACTIVE_MSG_DELAY_SEC с момента принятия
    """
    try:
        # Только для accepted заказов
        if order.status != "accepted":
            return

        # Проверяем: мы уже писали в этот чат?
        async with async_session() as session:
            db_messages = await get_messages_for_order(session, order.id)
        has_outgoing = any(m.direction == "outgoing" for m in db_messages)
        if has_outgoing:
            return  # Уже писали — не нужно

        # Проверяем: прошло ли 5 минут с момента принятия?
        updated_at = order.updated_at
        if updated_at is None:
            return  # Не знаем когда принят — подождём следующего цикла
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        elapsed = (datetime.now() - updated_at).total_seconds()
        if elapsed < PROACTIVE_MSG_DELAY_SEC:
            return  # Рано ещё

        # Генерируем проактивное сообщение
        proactive = await generate_proactive_fn(
            work_type=order.work_type or "",
            subject=order.subject or "",
            title=order.title or "",
            description=order.description or order.title or "",
            required_uniqueness=order.required_uniqueness,
            antiplagiat_system=order.antiplagiat_system or "",
        )

        await browser_manager.random_delay(min_sec=3, max_sec=8)
        send_ok = await _retry_async(send_message_fn, page, avtor24_id, proactive.text)

        if send_ok:
            async with async_session() as session:
                await create_message(
                    session,
                    order_id=order.id,
                    direction="outgoing",
                    text=proactive.text,
                    is_auto_reply=True,
                )

                await track_api_usage(
                    session,
                    model=settings.openai_model_fast,
                    purpose="chat",
                    input_tokens=proactive.input_tokens,
                    output_tokens=proactive.output_tokens,
                    cost_usd=proactive.cost_usd,
                    order_id=order.id,
                )

                await push_notification(
                    session,
                    type="new_message",
                    title=f"Проактивное сообщение: заказ #{avtor24_id}",
                    body={
                        "order_id": avtor24_id,
                        "customer_message": "(заказчик молчит)",
                        "auto_reply": proactive.text,
                        "auto_replied": True,
                    },
                    order_id=order.id,
                )

            await _log_action(
                "chat",
                f"Проактивное сообщение отправлено в заказ #{avtor24_id}: "
                f"\"{proactive.text[:100]}\"",
                order_id=order.id,
            )
        else:
            await _log_action(
                "error",
                f"Не удалось отправить проактивное сообщение в #{avtor24_id}",
                order_id=order.id,
            )

    except Exception as e:
        logger.warning("Ошибка проактивного сообщения для %s: %s", avtor24_id, e)


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
    from src.chat_ai.responder import generate_response, parse_customer_answer, generate_proactive_message, classify_assistant_messages
    from src.database.crud import update_order_fields

    await _track_task()
    _page_locked = False
    try:
        page = await _retry_async(login)
        await browser_manager.page_lock.acquire()
        _page_locked = True

        active_chats = await _retry_async(get_active_chats, page)
        if not active_chats:
            return

        await _log_action("chat", f"Найдено {len(active_chats)} чатов с новыми сообщениями")

        for avtor24_id in active_chats:
            if _shutting_down or not bot_running:
                break
            try:
                # Ищем заказ в БД; если нет — парсим и создаём
                async with async_session() as session:
                    order = await get_order_by_avtor24_id(session, avtor24_id)
                if not order:
                    order = await _ensure_order_in_db(page, avtor24_id, status="accepted")
                    if not order:
                        continue

                # Пропускаем завершённые/отменённые заказы
                # "delivered" НЕ пропускаем — заказчик может просить правки,
                # а условия могли измениться через Ассистента.
                if order.status in ("completed", "rejected", "cancelled"):
                    logger.debug("Чат %s пропущен: статус '%s'", avtor24_id, order.status)
                    continue

                # Получаем историю сообщений
                await browser_manager.random_delay(min_sec=2, max_sec=5)
                chat_messages = await _retry_async(get_messages, page, avtor24_id)
                if not chat_messages:
                    continue

                # --- Обработка сообщений Ассистента (изменение условий заказа) ---
                # GPT-4o-mini классифицирует сообщения: какие от платформы
                # Дедупликация: сначала фильтруем уже обработанные по хешу текста
                seen = _processed_assistant_msgs.get(avtor24_id, set())
                candidate_msgs = [
                    {"index": i, "text": m.text}
                    for i, m in enumerate(chat_messages)
                    if not m.is_system and hash(m.text.strip()) not in seen
                ]
                # Классификация через GPT (только если есть непроверенные)
                assistant_indices: set[int] = set()
                if candidate_msgs:
                    try:
                        classified = await classify_assistant_messages(candidate_msgs)
                        assistant_indices = set(classified)
                    except Exception as e:
                        logger.warning("GPT-классификация не удалась для %s: %s, фолбэк на хардкод", avtor24_id, e)
                        # Фолбэк: используем хардкод is_assistant
                        assistant_indices = {
                            i for i, m in enumerate(chat_messages) if m.is_assistant
                        }

                assistant_msgs = [chat_messages[i] for i in assistant_indices if i < len(chat_messages)]
                # Фильтруем уже обработанные
                new_assistant_msgs = [
                    m for m in assistant_msgs
                    if hash(m.text.strip()) not in seen
                ]
                if new_assistant_msgs:
                    prev_status = order.status
                    await _handle_assistant_messages(
                        page, avtor24_id, order, new_assistant_msgs,
                    )
                    # Запоминаем обработанные сообщения
                    if avtor24_id not in _processed_assistant_msgs:
                        _processed_assistant_msgs[avtor24_id] = set()
                    for m in new_assistant_msgs:
                        _processed_assistant_msgs[avtor24_id].add(hash(m.text.strip()))
                    # Перечитываем заказ из БД (мог обновиться / отмениться / сброситься)
                    async with async_session() as session:
                        order = await get_order_by_avtor24_id(session, avtor24_id)
                    if not order:
                        continue
                    # Если заказ был отменён или отправлен на перегенерацию — не отвечаем в чат
                    if order.status == "cancelled":
                        continue
                    if order.status == "accepted" and prev_status in (
                        "delivered", "ready", "generating", "checking_plagiarism", "rewriting", "error",
                    ):
                        # Сброшен на перегенерацию — не пишем в чат,
                        # process_accepted_orders_job перепарсит и сгенерирует заново
                        await _log_action(
                            "chat",
                            f"Чат #{avtor24_id}: условия изменены, ответ отложен до перегенерации",
                            order_id=order.id,
                        )
                        continue

                # Последнее сообщение — от заказчика?
                last_msg = chat_messages[-1]
                last_idx = len(chat_messages) - 1
                if last_idx in assistant_indices:
                    continue  # Ассистент — не отвечаем

                if not last_msg.is_incoming:
                    # Последнее сообщение — наше или системное.
                    # Проверяем: может, нужно проактивно написать первым?
                    await _maybe_send_proactive_message(
                        page, avtor24_id, order, chat_messages,
                        browser_manager, send_message,
                        generate_proactive_message,
                    )
                    continue

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
        if _page_locked:
            browser_manager.page_lock.release()
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
    """Проверить, приняли ли заказчики наши ставки (перевод bid_placed → accepted).

    Проверяет /home — раздел «Активные» (активные чаты) содержит принятые заказы.
    """
    if not bot_running or _shutting_down:
        return

    if is_banned():
        return

    from src.scraper.auth import login
    from src.scraper.browser import browser_manager
    from src.scraper.chat import get_accepted_order_ids, get_waiting_confirmation_order_ids, confirm_order

    await _track_task()
    _page_locked = False
    try:
        page = await _retry_async(login)
        await browser_manager.page_lock.acquire()
        _page_locked = True

        # --- Шаг 1: Подтверждение заказов «Ждёт подтверждения» ---
        waiting_ids = await _retry_async(get_waiting_confirmation_order_ids, page)
        if waiting_ids:
            await _log_action(
                "accept",
                f"Найдено {len(waiting_ids)} заказов «Ждёт подтверждения» — подтверждаем",
            )
            for wid in waiting_ids:
                if _shutting_down or not bot_running:
                    break
                try:
                    await browser_manager.random_delay(min_sec=2, max_sec=5)
                    confirmed = await _retry_async(confirm_order, page, wid)
                    if confirmed:
                        await _log_action("accept", f"Заказ #{wid} подтверждён (кнопка «Подтвердить»)")
                        # Убеждаемся что заказ в БД и в статусе accepted
                        order = await _ensure_order_in_db(page, wid, status="accepted")
                        if order and order.status == "bid_placed":
                            async with async_session() as session:
                                await update_order_status(session, order.id, "accepted")
                    else:
                        await _log_action(
                            "error",
                            f"Не удалось подтвердить заказ #{wid} (кнопка не найдена?)",
                        )
                except Exception as e:
                    logger.warning("Ошибка подтверждения заказа %s: %s", wid, e)

            # Возвращаемся на /home для следующего шага
            await page.goto(
                f"{settings.avtor24_base_url}/home",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(5)

        # --- Шаг 2: Перевод bid_placed → accepted для уже подтверждённых ---
        pending_order_ids = await _retry_async(get_accepted_order_ids, page)

        if not pending_order_ids:
            return

        await _log_action("accept", f"Найдено {len(pending_order_ids)} заказов в «Активные» на /home")

        for oid in pending_order_ids:
            if _shutting_down or not bot_running:
                break

            try:
                # Проверяем в БД — если bid_placed, значит заказчик принял нашу ставку
                async with async_session() as session:
                    order = await get_order_by_avtor24_id(session, oid)

                if not order:
                    # Заказа нет в БД — парсим и создаём (ставку ставили вручную?)
                    order = await _ensure_order_in_db(page, oid, status="accepted")
                    if not order:
                        continue
                    # Только что создали со статусом accepted — статистику обновим ниже
                elif order.status == "accepted":
                    # Уже accepted — не переводим повторно
                    continue
                elif order.status in ("error", "generating", "checking_plagiarism", "rewriting"):
                    # Заказ был в обработке, но что-то пошло не так — повторяем
                    await browser_manager.random_delay(min_sec=2, max_sec=5)
                    try:
                        await _retry_async(confirm_order, page, oid)
                    except Exception:
                        pass  # Кнопка "Подтвердить" может отсутствовать
                    async with async_session() as session:
                        await update_order_status(session, order.id, "accepted")
                    await _log_action(
                        "accept",
                        f"Заказ #{oid} сброшен из '{order.status}' → 'accepted' (повтор)",
                        order_id=order.id,
                    )
                elif order.status != "bid_placed":
                    # Статус не bid_placed и не accepted — не трогаем (delivered, completed, etc.)
                    continue
                else:
                    # bid_placed → accepted
                    async with async_session() as session:
                        await update_order_status(session, order.id, "accepted")

                async with async_session() as session:
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

                # Отправляем уточняющее сообщение
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
                logger.warning("Ошибка проверки статуса заказа %s: %s", oid, e)
                continue

    except Exception as e:
        logger.error("Ошибка проверки принятых заказов: %s", e)
        await _log_action("error", f"Ошибка проверки принятых ставок: {e}")
    finally:
        if _page_locked:
            browser_manager.page_lock.release()
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
