"""Мониторинг принятых заказов — реальный тест.

Скрипт работает в бесконечном цикле:
1. Для каждого заказа с bid_placed: проверяет страницу /order/getoneorder/{id}
2. Если "Вас выбрали автором" → заказ принят
3. Отправляет уточняющее сообщение в чат
4. Нажимает "Подтвердить" для начала работы
5. Генерация → антиплагиат → DOCX → отправка
6. Проверяет новые сообщения и отвечает
"""

import asyncio
import logging
import os
import random
import sys
from datetime import date, datetime
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.database.connection import async_session
from src.database.crud import (
    get_orders_by_status,
    get_order_by_avtor24_id,
    update_order_status,
    update_order_fields,
    create_message,
    track_api_usage,
    get_messages_for_order,
    get_daily_stats,
    upsert_daily_stats,
)
from src.scraper.auth import login
from src.scraper.browser import browser_manager
from src.scraper.chat import (
    get_order_page_info,
    get_messages,
    send_message,
    send_file_with_message,
    confirm_order,
)
from src.generator.router import generate_and_check
from src.docgen.builder import build_docx
from src.scraper.file_handler import upload_file
from src.chat_ai.responder import (
    generate_response,
    generate_clarifying_message,
    parse_customer_answer,
    detect_customer_approval,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor")

# Интервал проверки (секунды)
CHECK_INTERVAL = 30


# ========== Humanization: реалистичные задержки ==========
# В тестовом режиме (TEST_MODE=True) задержки минимальные.
# В продакшене задержки имитируют реального автора.
TEST_MODE = os.environ.get("TEST_MODE", "0") == "1" or "--test" in sys.argv

# Задержки (секунды)
DELAY_BEFORE_CLARIFY_MSG = (60, 180) if not TEST_MODE else (3, 5)       # 1-3 мин перед уточнением
DELAY_BEFORE_CONFIRM = (30, 90) if not TEST_MODE else (2, 3)            # 0.5-1.5 мин перед подтверждением
DELAY_WORK_PER_PAGE = (300, 600) if not TEST_MODE else (5, 10)          # 5-10 мин на страницу "работы"
DELAY_BEFORE_DELIVERY = (120, 300) if not TEST_MODE else (3, 5)         # 2-5 мин перед отправкой файла
DELAY_BEFORE_CHAT_REPLY = (60, 300) if not TEST_MODE else (2, 5)        # 1-5 мин перед ответом в чат


async def human_delay(delay_range: tuple[int, int], description: str = "") -> None:
    """Подождать случайное количество секунд (имитация человека)."""
    seconds = random.randint(delay_range[0], delay_range[1])
    if description:
        mins = seconds // 60
        secs = seconds % 60
        if mins > 0:
            safe_print(f"    [DELAY] {description}: ожидание {mins} мин {secs} сек...")
        else:
            safe_print(f"    [DELAY] {description}: ожидание {secs} сек...")
    await asyncio.sleep(seconds)


def safe_print(text: str) -> None:
    """Безопасный print для Windows."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def caps(text: str) -> None:
    """Вывести сообщение КАПСОМ с рамкой."""
    border = "=" * 60
    safe_print(f"\n{border}")
    safe_print(text.upper())
    safe_print(f"{border}\n")


def step(num: int, total: int, text: str) -> None:
    """Вывести шаг выполнения."""
    safe_print(f"  [STEP {num}/{total}] {text}")


async def check_order_acceptance(page, order) -> dict | None:
    """Проверить страницу заказа на признаки принятия.

    Returns dict с информацией о странице или None если не принят.
    """
    try:
        info = await get_order_page_info(page, order.avtor24_id)

        if info.get("error"):
            safe_print(f"  [WARN] Ошибка чтения страницы #{order.avtor24_id}: {info['error']}")
            return None

        if info.get("accepted"):
            return info

        # Также проверяем: нет формы ставки + есть чат = скорее всего принят
        if not info.get("hasBidForm") and info.get("hasChat"):
            page_text = info.get("pageText", "")
            if "Вас выбрали" in page_text or "Подтвердить" in page_text:
                return info

        return None
    except Exception as e:
        safe_print(f"  [WARN] Ошибка проверки #{order.avtor24_id}: {e}")
        return None


async def process_accepted_order(page, order) -> None:
    """Полный пайплайн обработки принятого заказа."""
    total_steps = 7

    caps(f"ЗАКАЗ #{order.avtor24_id} ПРИНЯТ! НАЧИНАЮ ОБРАБОТКУ")
    safe_print(f"  Тема: {order.title}")
    safe_print(f"  Тип: {order.work_type}")
    safe_print(f"  Предмет: {order.subject}")
    safe_print(f"  Ставка: {order.bid_price} RUB")
    safe_print("")

    # Обновляем статус в БД
    async with async_session() as session:
        await update_order_status(session, order.id, "accepted")
        today = date.today()
        stats = await get_daily_stats(session, today)
        await upsert_daily_stats(
            session, today,
            orders_accepted=(stats.orders_accepted if stats else 0) + 1,
        )

    # STEP 1: Подтверждение заказа (кнопка "Подтвердить")
    # Небольшая пауза перед подтверждением — как будто читаем описание
    await human_delay(DELAY_BEFORE_CONFIRM, "читаю описание заказа")
    step(1, total_steps, "Подтверждение начала работы (кнопка 'Подтвердить')...")
    try:
        confirmed = await confirm_order(page, order.avtor24_id)
        if confirmed:
            safe_print("    -> Заказ подтверждён!")
        else:
            safe_print("    -> Кнопка 'Подтвердить' не найдена (возможно уже подтверждён)")
    except Exception as e:
        safe_print(f"    -> Ошибка подтверждения: {e}")

    # STEP 2: Уточняющее сообщение в чат
    await human_delay(DELAY_BEFORE_CLARIFY_MSG, "формулирую уточняющий вопрос")
    step(2, total_steps, "Отправка уточняющего сообщения в чат...")
    try:
        clarify = await generate_clarifying_message(
            work_type=order.work_type or "",
            subject=order.subject or "",
            title=order.title,
            description=order.description or "",
            required_uniqueness=order.required_uniqueness,
            antiplagiat_system=order.antiplagiat_system or "",
            bid_price=order.bid_price or 0,
        )
        send_ok = await send_message(page, order.avtor24_id, clarify.text)
        if send_ok:
            async with async_session() as session:
                await create_message(
                    session, order_id=order.id, direction="outgoing",
                    text=clarify.text, is_auto_reply=True,
                )
            safe_print(f"    -> Отправлено: {clarify.text[:100]}")
        else:
            safe_print("    -> Не удалось отправить (селектор не найден)")
    except Exception as e:
        safe_print(f"    -> Ошибка: {e}")

    # STEP 3: Генерация работы (с имитацией времени "написания")
    # Сначала генерируем, потом ждём реалистичное время
    step(3, total_steps, "Генерация работы через GPT-4o...")
    async with async_session() as session:
        await update_order_status(session, order.id, "generating")

    antiplagiat_sys = order.antiplagiat_system or "textru"
    req_uniq = order.required_uniqueness or settings.min_uniqueness

    gen_result, check_result = await generate_and_check(
        work_type=order.work_type or "Эссе",
        title=order.title,
        description=order.description or "",
        subject=order.subject or "",
        pages=order.pages_max or order.pages_min,
        required_uniqueness=req_uniq,
        font_size=order.font_size or 14,
        line_spacing=order.line_spacing or 1.5,
        antiplagiat_system=antiplagiat_sys,
    )

    if gen_result is None:
        caps("ОШИБКА: ГЕНЕРАЦИЯ НЕ УДАЛАСЬ")
        async with async_session() as session:
            await update_order_status(
                session, order.id, "error",
                error_message="Генерация не удалась",
            )
        return

    safe_print(f"    -> Сгенерировано ~{gen_result.pages_approx} стр, ${gen_result.cost_usd:.2f}")

    # Трекинг API
    async with async_session() as session:
        await track_api_usage(
            session, model=settings.openai_model_main, purpose="generation",
            input_tokens=gen_result.input_tokens, output_tokens=gen_result.output_tokens,
            cost_usd=gen_result.cost_usd, order_id=order.id,
        )

    # Имитация времени "написания" — пропорционально количеству страниц
    pages_est = gen_result.pages_approx or 5
    work_delay = (
        DELAY_WORK_PER_PAGE[0] * pages_est,
        DELAY_WORK_PER_PAGE[1] * pages_est,
    )
    await human_delay(work_delay, f"имитация написания ~{pages_est} стр.")

    # STEP 4: Антиплагиат
    step(4, total_steps, "Проверка антиплагиат через text.ru API...")
    uniqueness = check_result.uniqueness if check_result else 0.0
    is_ok = check_result.is_sufficient if check_result else False
    is_sampled = getattr(check_result, "is_sampled", False) if check_result else False
    check_type = "Выборочная" if is_sampled else "Полная"
    if is_ok:
        safe_print(f"    -> {check_type} проверка: {uniqueness:.1f}% (порог {req_uniq}%) OK — полная проверка не требуется" if is_sampled else f"    -> {check_type} проверка: {uniqueness:.1f}% (порог {req_uniq}%) OK")
    else:
        safe_print(f"    -> {check_type} проверка: {uniqueness:.1f}% (порог {req_uniq}%) НИЗКАЯ")

    async with async_session() as session:
        await update_order_status(
            session, order.id, "checking_plagiarism",
            uniqueness_percent=uniqueness,
            api_cost_usd=gen_result.cost_usd,
            api_tokens_used=gen_result.total_tokens,
        )

    # STEP 5: Сборка DOCX
    step(5, total_steps, "Сборка DOCX файла...")
    docx_path = await build_docx(
        title=order.title,
        text=gen_result.text,
        work_type=order.work_type or "Реферат",
        subject=order.subject or "",
        font_size=order.font_size or 14,
        line_spacing=order.line_spacing or 1.5,
    )

    if docx_path is None:
        caps("ОШИБКА: НЕ УДАЛОСЬ СОБРАТЬ DOCX")
        async with async_session() as session:
            await update_order_status(
                session, order.id, "error",
                error_message="Не удалось собрать DOCX",
            )
        return

    safe_print(f"    -> DOCX создан: {docx_path}")

    # STEP 6: Загрузка файла как ПРОМЕЖУТОЧНЫЙ вариант (на проверку заказчику)
    await human_delay(DELAY_BEFORE_DELIVERY, "подготовка к отправке")
    step(6, total_steps, "Загрузка DOCX как Промежуточный вариант (на проверку)...")

    # Генерируем сопроводительное сообщение (с указанием % уникальности)
    uniq_info = ""
    if uniqueness > 0 and req_uniq > 0:
        uniq_info = f" Уникальность по {antiplagiat_sys}: {uniqueness:.0f}% (при требуемых {req_uniq}%)."
    try:
        delivery_response = await generate_response(
            order_description=order.description or order.title,
            message_history=[],
            new_message=(
                "Работа готова, загружаю промежуточный вариант на проверку. "
                "Напиши короткое сообщение заказчику — попроси проверить и сказать, если нужны правки. "
                "Если всё ок — попроси подтвердить."
                f"{uniq_info}"
                " Если была проверена уникальность — упомяни процент в сообщении."
                " НЕ пиши что уже загрузил файл."
            ),
            order_status="delivering",
            work_type=order.work_type or "",
            subject=order.subject or "",
        )
        delivery_msg = delivery_response.text
    except Exception:
        delivery_msg = f"Работа готова!{uniq_info} Посмотрите, если нужны правки — пишите, если всё ок — подтверждайте."

    safe_print(f"    -> Сопроводительное: {delivery_msg[:80]}")

    send_ok = await send_file_with_message(
        page, order.avtor24_id, str(docx_path), delivery_msg,
        variant="intermediate",
    )

    if send_ok:
        safe_print("    -> Файл загружен как Промежуточный вариант!")
    else:
        safe_print("    -> Не удалось загрузить файл, отправляем сообщение...")
        await send_message(page, order.avtor24_id, delivery_msg)

    # STEP 7: Обновление БД — статус "awaiting_approval" (ждём одобрения)
    step(7, total_steps, "Обновление базы данных...")
    async with async_session() as session:
        await update_order_status(
            session, order.id, "awaiting_approval",
            generated_file_path=str(docx_path),
            api_cost_usd=gen_result.cost_usd,
            api_tokens_used=gen_result.total_tokens,
        )
        await create_message(
            session, order_id=order.id, direction="outgoing",
            text=delivery_msg, is_auto_reply=True,
        )
        today = date.today()
        stats = await get_daily_stats(session, today)
        await upsert_daily_stats(
            session, today,
            api_cost_usd=(stats.api_cost_usd if stats else 0) + gen_result.cost_usd,
            api_tokens_used=(stats.api_tokens_used if stats else 0) + gen_result.total_tokens,
        )

    caps(f"ЗАКАЗ #{order.avtor24_id} ОТПРАВЛЕН НА ПРОВЕРКУ!")
    safe_print(f"  Уникальность: {uniqueness:.1f}%")
    safe_print(f"  Стоимость API: ${gen_result.cost_usd:.2f}")
    safe_print(f"  Ожидаю одобрения заказчика...")
    safe_print(f"  При одобрении → автоматически загружу как Окончательный")
    safe_print("")


async def _handle_awaiting_approval(page, order, last_msg, real_messages) -> None:
    """Обработка сообщения заказчика для заказа в статусе awaiting_approval.

    - approve → загрузить как Окончательный, обновить статус
    - revise  → ответить AI, оставить статус awaiting_approval
    - other   → обычный ответ AI
    """
    context_str = (
        f"Тип: {order.work_type}, Предмет: {order.subject}, "
        f"Тема: {order.title}, Статус: промежуточный вариант отправлен"
    )

    # Определяем намерение заказчика
    try:
        result = await detect_customer_approval(
            customer_text=last_msg.text,
            order_context=context_str,
        )
        action = result.get("action", "other")
        details = result.get("details", "")
        safe_print(f"  [APPROVAL] Намерение: {action} ({details})")
    except Exception as e:
        logger.warning("Ошибка detect_customer_approval: %s", e)
        action = "other"

    # --- ACTION: APPROVE → загрузить как Окончательный ---
    if action == "approve":
        caps(f"ЗАКАЗЧИК ОДОБРИЛ ЗАКАЗ #{order.avtor24_id}! ЗАГРУЖАЮ ОКОНЧАТЕЛЬНЫЙ")

        # Загружаем как Окончательный (СНАЧАЛА загрузка, ПОТОМ сообщение)
        file_path = order.generated_file_path
        if file_path and Path(file_path).exists():
            await human_delay(DELAY_BEFORE_DELIVERY, "подготовка окончательного варианта")
            safe_print(f"  [UPLOAD] Загружаю {Path(file_path).name} как Окончательный...")

            upload_ok = await upload_file(
                page, order.avtor24_id, Path(file_path), variant="final",
            )

            if upload_ok:
                safe_print("  [UPLOAD] -> Окончательный вариант загружен!")

                # Отправляем сообщение с просьбой об отзыве
                try:
                    message_history = []
                    for msg in real_messages:
                        role = "user" if msg.is_incoming else "assistant"
                        message_history.append({"role": role, "content": msg.text})

                    review_response = await generate_response(
                        order_description=order.description or order.title,
                        message_history=message_history,
                        new_message=(
                            "Заказчик одобрил работу, ты загрузил окончательный вариант. "
                            "Напиши короткое сообщение: поблагодари за работу, "
                            "и ОБЯЗАТЕЛЬНО вежливо попроси оставить отзыв — "
                            "это важно для рейтинга на платформе. "
                            "2-3 предложения максимум. НЕ пиши что загрузил файл."
                        ),
                        order_status="delivered",
                        work_type=order.work_type or "",
                        subject=order.subject or "",
                    )
                    await human_delay(DELAY_BEFORE_CHAT_REPLY, "пишу сообщение")
                    send_ok = await send_message(page, order.avtor24_id, review_response.text)
                    if send_ok:
                        async with async_session() as session:
                            await create_message(
                                session, order_id=order.id, direction="outgoing",
                                text=review_response.text, is_auto_reply=True,
                            )
                        safe_print(f"  [CHAT] -> Просьба об отзыве: {review_response.text[:80]}")
                except Exception as e:
                    logger.warning("Ошибка отправки просьбы об отзыве: %s", e)

                async with async_session() as session:
                    income = int(order.bid_price * 0.97) if order.bid_price else 0
                    await update_order_status(
                        session, order.id, "delivered",
                        income_rub=income,
                    )
                    today = date.today()
                    stats = await get_daily_stats(session, today)
                    await upsert_daily_stats(
                        session, today,
                        orders_delivered=(stats.orders_delivered if stats else 0) + 1,
                        income_rub=(stats.income_rub if stats else 0) + income,
                    )
                caps(f"ЗАКАЗ #{order.avtor24_id} ЗАВЕРШЁН! ДОХОД: {income} RUB")
            else:
                safe_print("  [UPLOAD] -> Ошибка загрузки окончательного варианта")
        else:
            safe_print(f"  [UPLOAD] -> Файл не найден: {file_path}")

    # --- ACTION: REVISE → нужны правки ---
    elif action == "revise":
        safe_print(f"  [REVISE] Заказчик просит правки: {details}")

        # Генерируем ответ
        message_history = []
        for msg in real_messages[:-1]:
            role = "user" if msg.is_incoming else "assistant"
            message_history.append({"role": role, "content": msg.text})

        ai_response = await generate_response(
            order_description=order.description or order.title,
            message_history=message_history,
            new_message=last_msg.text,
            order_status="awaiting_approval",
            work_type=order.work_type or "",
            subject=order.subject or "",
            deadline=str(order.deadline) if order.deadline else "",
            required_uniqueness=order.required_uniqueness,
            antiplagiat_system=order.antiplagiat_system or "",
            bid_price=order.bid_price,
        )

        send_ok = await send_message(page, order.avtor24_id, ai_response.text)
        if send_ok:
            async with async_session() as session:
                await create_message(
                    session, order_id=order.id, direction="outgoing",
                    text=ai_response.text, is_auto_reply=True,
                )
                await track_api_usage(
                    session, model=settings.openai_model_fast, purpose="chat",
                    input_tokens=ai_response.input_tokens,
                    output_tokens=ai_response.output_tokens,
                    cost_usd=ai_response.cost_usd, order_id=order.id,
                )
            safe_print(f"  [CHAT] -> Ответ: {ai_response.text[:80]}")

        # TODO: В будущем — автоматическая перегенерация по замечаниям
        safe_print("  [REVISE] Заказ остаётся в статусе awaiting_approval")

    # --- ACTION: OTHER → обычный ответ ---
    else:
        message_history = []
        for msg in real_messages[:-1]:
            role = "user" if msg.is_incoming else "assistant"
            message_history.append({"role": role, "content": msg.text})

        ai_response = await generate_response(
            order_description=order.description or order.title,
            message_history=message_history,
            new_message=last_msg.text,
            order_status="awaiting_approval",
            work_type=order.work_type or "",
            subject=order.subject or "",
        )

        send_ok = await send_message(page, order.avtor24_id, ai_response.text)
        if send_ok:
            async with async_session() as session:
                await create_message(
                    session, order_id=order.id, direction="outgoing",
                    text=ai_response.text, is_auto_reply=True,
                )
                await track_api_usage(
                    session, model=settings.openai_model_fast, purpose="chat",
                    input_tokens=ai_response.input_tokens,
                    output_tokens=ai_response.output_tokens,
                    cost_usd=ai_response.cost_usd, order_id=order.id,
                )
            safe_print(f"  [CHAT] -> Ответ: {ai_response.text[:80]}")


async def check_and_reply_chats(page) -> None:
    """Проверить новые сообщения на страницах заказов и ответить."""
    try:
        # Берём все заказы которые в активных статусах
        async with async_session() as session:
            active_orders = []
            for status in ["bid_placed", "accepted", "generating", "delivered", "awaiting_approval"]:
                orders = await get_orders_by_status(session, status)
                active_orders.extend(orders)

        if not active_orders:
            return

        for order in active_orders:
            try:
                # Читаем чат на странице заказа
                chat_messages = await get_messages(page, order.avtor24_id)
                if not chat_messages:
                    continue

                # Фильтруем только не-системные сообщения
                real_messages = [m for m in chat_messages if not m.is_system]
                if not real_messages:
                    continue

                # Загружаем наши исходящие из БД для корректной фильтрации направления
                # (styled-components не дают надёжно отличить incoming/outgoing)
                async with async_session() as session:
                    db_messages = await get_messages_for_order(session, order.id)
                    our_texts = set()
                    for m in db_messages:
                        if m.direction == "outgoing":
                            our_texts.add(m.text[:50])

                # Помечаем сообщения, совпадающие с нашими, как outgoing
                for msg in real_messages:
                    if msg.text[:50] in our_texts:
                        msg.is_incoming = False

                last_msg = real_messages[-1]
                if not last_msg.is_incoming:
                    continue

                # Проверяем не отвечали ли мы уже на это сообщение
                async with async_session() as session:
                    db_messages = await get_messages_for_order(session, order.id)
                    last_db_incoming = None
                    for m in reversed(db_messages):
                        if m.direction == "incoming":
                            last_db_incoming = m
                            break

                    # Если последнее входящее уже есть в БД — пропускаем
                    if last_db_incoming and last_msg.text[:50] == last_db_incoming.text[:50]:
                        continue

                # Сохраняем входящее
                async with async_session() as session:
                    await create_message(
                        session, order_id=order.id, direction="incoming",
                        text=last_msg.text,
                    )

                safe_print(f"  [CHAT] #{order.avtor24_id} Заказчик: {last_msg.text[:80]}")

                # Задержка перед ответом — как будто читаем и думаем
                await human_delay(DELAY_BEFORE_CHAT_REPLY, "читаю сообщение")

                # === APPROVAL DETECTION для заказов в статусе awaiting_approval ===
                if order.status == "awaiting_approval":
                    await _handle_awaiting_approval(
                        page, order, last_msg, real_messages,
                    )
                    continue

                # Парсинг ответа (обновление полей если не хватает)
                if not order.antiplagiat_system or not order.required_uniqueness:
                    try:
                        context_str = f"Тип: {order.work_type}, Предмет: {order.subject}, Тема: {order.title}"
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
                            async with async_session() as session:
                                order = await get_order_by_avtor24_id(session, order.avtor24_id)
                            safe_print(f"  [CHAT] Обновлены поля: {update_kwargs}")
                    except Exception as e:
                        logger.warning("Ошибка парсинга ответа: %s", e)

                # Формируем историю
                message_history = []
                for msg in real_messages[:-1]:
                    role = "user" if msg.is_incoming else "assistant"
                    message_history.append({"role": role, "content": msg.text})

                # Генерируем ответ с полным контекстом
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
                )

                # Отправляем
                send_ok = await send_message(page, order.avtor24_id, ai_response.text)
                if send_ok:
                    async with async_session() as session:
                        await create_message(
                            session, order_id=order.id, direction="outgoing",
                            text=ai_response.text, is_auto_reply=True,
                        )
                        await track_api_usage(
                            session, model=settings.openai_model_fast, purpose="chat",
                            input_tokens=ai_response.input_tokens,
                            output_tokens=ai_response.output_tokens,
                            cost_usd=ai_response.cost_usd, order_id=order.id,
                        )
                    safe_print(f"  [CHAT] -> Ответ: {ai_response.text[:80]}")
                else:
                    safe_print(f"  [CHAT] -> Не удалось отправить ответ #{order.avtor24_id}")

            except Exception as e:
                logger.warning("Ошибка чата для #%s: %s", order.avtor24_id, e)

    except Exception as e:
        logger.warning("Ошибка проверки чатов: %s", e)


async def main():
    """Главный цикл мониторинга."""
    safe_print("")
    caps("ЗАПУСК МОНИТОРИНГА ПРИНЯТЫХ ЗАКАЗОВ")
    safe_print(f"  Интервал проверки: {CHECK_INTERVAL} сек")
    safe_print(f"  Avtor24: {settings.avtor24_base_url}")
    safe_print(f"  Прокси: {'да' if settings.proxy_ru else 'нет'}")
    safe_print(f"  Метод: проверяем /order/getoneorder/{{id}} каждого заказа")
    safe_print("")

    # Авторизация
    safe_print("[LOGIN] Авторизация на Avtor24...")
    page = await login()
    safe_print("[LOGIN] Успешно!")
    safe_print("")

    # Показать текущие заказы со ставками
    async with async_session() as session:
        bid_orders = await get_orders_by_status(session, "bid_placed")

    if bid_orders:
        safe_print(f"[INFO] Заказов со ставками: {len(bid_orders)}")
        for o in bid_orders:
            customer = o.customer_username or "?"
            safe_print(f"  - #{o.avtor24_id}: {o.title[:50]} (от {customer}, {o.bid_price} RUB)")
        safe_print("")
    else:
        safe_print("[INFO] Нет заказов со ставками в БД")
        safe_print("")

    caps("ПРИМИ СТАВКУ НА ЗАКАЗ ОТ OCAVIT")
    safe_print("Ожидаю принятия ставки...")
    safe_print(f"Проверяю каждые {CHECK_INTERVAL} секунд...\n")

    cycle = 0
    while True:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        safe_print(f"[{now}] Цикл #{cycle}:")

        try:
            # 1. Проверяем каждый заказ с bid_placed
            async with async_session() as session:
                bid_orders = await get_orders_by_status(session, "bid_placed")

            if bid_orders:
                safe_print(f"  Проверяю {len(bid_orders)} заказов со ставками...")
                for order in bid_orders:
                    safe_print(f"  -> #{order.avtor24_id}: открываю страницу заказа...")
                    info = await check_order_acceptance(page, order)
                    if info:
                        caps(f"ЗАКАЗ #{order.avtor24_id} ПРИНЯТ ЗАКАЗЧИКОМ!")
                        safe_print(f"  Текст страницы (ключевое):")
                        page_text = info.get("pageText", "")
                        for kw in ["Вас выбрали", "Подтвердить", "Ожидается"]:
                            if kw in page_text:
                                safe_print(f"    + '{kw}' найдено")
                        safe_print(f"  Кнопка Подтвердить: {info.get('hasConfirmBtn')}")
                        safe_print(f"  Сообщений в чате: {len(info.get('messages', []))}")

                        await process_accepted_order(page, order)
                    else:
                        safe_print(f"  -> #{order.avtor24_id}: ещё не принят")
            else:
                safe_print(f"  Нет заказов со ставками")

            # 2. Проверяем чаты
            safe_print(f"  Проверяю чаты...")
            await check_and_reply_chats(page)

        except Exception as e:
            safe_print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()
            # Попробуем переавторизоваться
            try:
                safe_print("  [REAUTH] Попытка переавторизации...")
                page = await login()
                safe_print("  [REAUTH] Успешно!")
            except Exception as re_e:
                safe_print(f"  [REAUTH] Ошибка: {re_e}")

        safe_print(f"  Следующая проверка через {CHECK_INTERVAL} сек...\n")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        safe_print("\n[STOP] Мониторинг остановлен (Ctrl+C)")
    finally:
        asyncio.run(browser_manager.close())
