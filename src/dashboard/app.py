"""Дашборд — FastAPI роутер со всеми API эндпоинтами."""

import csv
import io
import os
import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

# Московское время (UTC+3)
_MSK = timezone(timedelta(hours=3))

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings

# Путь к папке static
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
from src.database.connection import async_session
from src.database.crud import (
    get_order,
    get_orders_by_status,
    get_orders_paginated,
    get_messages_for_order,
    create_message,
    get_dashboard_stats,
    get_analytics,
    get_notifications_paginated,
    get_unread_notification_count,
    mark_notifications_read,
    get_action_logs_paginated,
    get_all_settings,
    set_many_settings,
    update_order_status,
)
from src.dashboard.auth import login as auth_login, get_current_user

router = APIRouter()

# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------

@router.post("/api/dashboard/login")
async def login_endpoint(request: Request):
    """Авторизация — выдача JWT cookie."""
    return await auth_login(request)


# ---------------------------------------------------------------------------
# Статистика (виджеты)
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/stats")
async def dashboard_stats(user: str = Depends(get_current_user)):
    """Виджеты: баланс, доходы, активные заказы, API расход."""
    async with async_session() as session:
        stats = await get_dashboard_stats(session)

    import src.main as main_module
    stats["bot_running"] = main_module.bot_running
    stats["uptime"] = int(time.time() - main_module.start_time)
    return stats


@router.post("/api/dashboard/bot/toggle")
async def toggle_bot(user: str = Depends(get_current_user)):
    """Включить/выключить бота."""
    import src.main as main_module

    main_module.bot_running = not main_module.bot_running
    new_state = main_module.bot_running

    # Pause/resume scheduler jobs to actually stop processing
    try:
        if new_state:
            main_module.scheduler.resume()
        else:
            main_module.scheduler.pause()
    except Exception:
        pass  # Scheduler may not support pause in all states

    # Log the action
    try:
        action = "system"
        details = f"Бот {'запущен' if new_state else 'остановлен'} через дашборд"
        async with async_session() as session:
            from src.database.crud import create_action_log
            await create_action_log(session, action=action, details=details)
        # Also broadcast to log websocket
        from src.notifications.websocket import log_manager
        await log_manager.broadcast({
            "action": action,
            "details": details,
            "order_id": None,
            "timestamp": datetime.now(_MSK).isoformat(),
        })
    except Exception:
        pass

    return {"ok": True, "bot_running": new_state}


# ---------------------------------------------------------------------------
# Заказы
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/orders")
async def orders_list(
    status: Optional[str] = Query(None),
    work_type: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    min_price: Optional[int] = Query(None),
    max_price: Optional[int] = Query(None),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: str = Depends(get_current_user),
):
    """Список заказов с фильтрацией, сортировкой и пагинацией."""
    offset = (page - 1) * per_page
    async with async_session() as session:
        orders, total = await get_orders_paginated(
            session,
            status=status,
            work_type=work_type,
            subject=subject,
            min_price=min_price,
            max_price=max_price,
            sort_by=sort_by,
            sort_dir=sort_dir,
            offset=offset,
            limit=per_page,
        )

    return {
        "items": [_order_to_dict(o) for o in orders],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
    }


@router.get("/api/dashboard/orders/{order_id}")
async def order_detail(order_id: int, user: str = Depends(get_current_user)):
    """Детали заказа + чат + логи + API usage."""
    async with async_session() as session:
        order = await get_order(session, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Заказ не найден")

        messages = await get_messages_for_order(session, order_id)

        from src.database.crud import get_action_logs_paginated
        logs, _ = await get_action_logs_paginated(session, offset=0, limit=50)
        order_logs = [l for l in logs if l.order_id == order_id]

        from sqlalchemy import select
        from src.database.models import ApiUsage
        api_result = await session.execute(
            select(ApiUsage).where(ApiUsage.order_id == order_id)
        )
        api_usages = list(api_result.scalars().all())

    return {
        "order": _order_to_dict(order),
        "messages": [
            {
                "id": m.id,
                "direction": m.direction,
                "text": m.text,
                "is_auto_reply": m.is_auto_reply,
                "created_at": _to_msk_iso(m.created_at),
            }
            for m in messages
        ],
        "logs": [
            {
                "id": l.id,
                "action": l.action,
                "details": l.details,
                "created_at": _to_msk_iso(l.created_at),
            }
            for l in order_logs
        ],
        "api_usage": [
            {
                "id": u.id,
                "model": u.model,
                "purpose": u.purpose,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cost_usd": round(u.cost_usd, 4),
                "created_at": _to_msk_iso(u.created_at),
            }
            for u in api_usages
        ],
    }


@router.post("/api/dashboard/orders/{order_id}/stop")
async def stop_order(order_id: int, user: str = Depends(get_current_user)):
    """Остановить обработку заказа."""
    async with async_session() as session:
        order = await get_order(session, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        await update_order_status(session, order_id, "rejected", error_message="Остановлено вручную")
    return {"ok": True, "order_id": order_id, "new_status": "rejected"}


@router.post("/api/dashboard/orders/{order_id}/regen")
async def regen_order(order_id: int, user: str = Depends(get_current_user)):
    """Перегенерировать работу — сбросить статус на accepted."""
    async with async_session() as session:
        order = await get_order(session, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Заказ не найден")

        prev_status = order.status
        await update_order_status(
            session, order_id, "accepted",
            generated_file_path=None, error_message=None,
        )

        # Логирование перегенерации
        from src.database.crud import create_action_log
        await create_action_log(
            session,
            action="generate",
            details=f"Перегенерация запущена вручную через дашборд (было '{prev_status}' → 'accepted')",
            order_id=order_id,
        )

    # WebSocket broadcast в логи
    try:
        from src.notifications.websocket import log_manager
        await log_manager.broadcast({
            "action": "generate",
            "details": f"Перегенерация заказа #{order.avtor24_id} запущена вручную",
            "order_id": order_id,
            "timestamp": datetime.now(_MSK).isoformat(),
        })
    except Exception:
        pass

    return {"ok": True, "order_id": order_id, "new_status": "accepted", "message": "Перегенерация запущена"}


# ---------------------------------------------------------------------------
# Аналитика
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/analytics")
async def analytics_endpoint(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user: str = Depends(get_current_user),
):
    """Аналитика по периоду: доход, заказы, токены, ROI."""
    d_from = date.fromisoformat(date_from) if date_from else None
    d_to = date.fromisoformat(date_to) if date_to else None

    async with async_session() as session:
        data = await get_analytics(session, date_from=d_from, date_to=d_to)
    return data


# ---------------------------------------------------------------------------
# Уведомления
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/notifications")
async def notifications_list(
    type_filter: Optional[str] = Query(None, alias="type"),
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: str = Depends(get_current_user),
):
    """Список уведомлений."""
    offset = (page - 1) * per_page
    async with async_session() as session:
        notifications, total = await get_notifications_paginated(
            session,
            type_filter=type_filter,
            unread_only=unread_only,
            offset=offset,
            limit=per_page,
        )
        unread_count = await get_unread_notification_count(session)

    return {
        "items": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "order_id": n.order_id,
                "is_read": n.is_read,
                "created_at": _to_msk_iso(n.created_at),
            }
            for n in notifications
        ],
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "per_page": per_page,
    }


@router.post("/api/dashboard/notifications/read")
async def mark_read(request: Request, user: str = Depends(get_current_user)):
    """Пометить уведомления как прочитанные."""
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids обязателен")
    async with async_session() as session:
        await mark_notifications_read(session, ids)
    return {"ok": True, "marked": len(ids)}


# ---------------------------------------------------------------------------
# Логи
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/logs")
async def logs_list(
    action: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    user: str = Depends(get_current_user),
):
    """Логи действий бота."""
    offset = (page - 1) * per_page
    async with async_session() as session:
        logs, total = await get_action_logs_paginated(
            session,
            action_filter=action,
            search=search,
            offset=offset,
            limit=per_page,
        )

    return {
        "items": [
            {
                "id": l.id,
                "action": l.action,
                "order_id": l.order_id,
                "details": l.details,
                "created_at": _to_msk_iso(l.created_at),
            }
            for l in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

# Дефолтные настройки бота (вставляются если отсутствуют)
DEFAULT_SETTINGS = {
    "auto_bid": "true",
    "scan_interval_seconds": "60",
    "max_concurrent_orders": "5",
    "min_score_for_bid": "60",
    "min_price_rub": "300",
    "max_price_rub": "50000",
    "speed_limit_min_delay": "30",
    "speed_limit_max_delay": "120",
    "max_bids_per_day": "20",
    "work_hours_start": "0",
    "work_hours_end": "24",
    "openai_model_main": "gpt-4o",
    "openai_model_fast": "gpt-4o-mini",
    "generation_temperature": "0.7",
    "max_api_budget_day_usd": "10",
    "antiplagiat_default_system": "textru",
    "max_rewrite_iterations": "3",
    "uniqueness_buffer_percent": "5",
    "bid_comment_template": "Добрый день! Тема знакома, имею опыт. Готов выполнить качественно и в срок.",
    "chat_greeting_template": "Здравствуйте! Готов приступить к работе.",
    "delivery_message_template": "Добрый день! Работа готова, загружаю файл. Если потребуются правки — пишите, исправлю.",
}


@router.get("/api/dashboard/settings")
async def get_settings(user: str = Depends(get_current_user)):
    """Текущие настройки бота."""
    async with async_session() as session:
        # Вставить дефолты если их нет
        current = await get_all_settings(session)
        for key, default_val in DEFAULT_SETTINGS.items():
            if key not in current:
                from src.database.crud import set_setting
                await set_setting(session, key, default_val)
                current[key] = default_val
    return current


@router.put("/api/dashboard/settings")
async def update_settings(request: Request, user: str = Depends(get_current_user)):
    """Обновить настройки бота."""
    body = await request.json()
    async with async_session() as session:
        await set_many_settings(session, body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Чат (ручное сообщение)
# ---------------------------------------------------------------------------

@router.post("/api/dashboard/chat/{order_id}/send")
async def chat_send(order_id: int, request: Request, user: str = Depends(get_current_user)):
    """Ручное сообщение в чат заказчика (override AI)."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text обязателен")

    async with async_session() as session:
        order = await get_order(session, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Заказ не найден")

        msg = await create_message(
            session,
            order_id=order_id,
            direction="outgoing",
            text=text,
            is_auto_reply=False,
        )

    # Попытаемся отправить через Playwright (если бот запущен)
    sent = False
    try:
        from src.scraper.auth import login
        from src.scraper.chat import send_message
        page = await login()
        sent = await send_message(page, order.avtor24_id, text)
    except Exception:
        pass  # Сообщение сохранено в БД, отправка может быть позже

    return {"ok": True, "message_id": msg.id, "sent_to_platform": sent}


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------

@router.get("/api/dashboard/export/csv")
async def export_csv(
    status: Optional[str] = Query(None),
    user: str = Depends(get_current_user),
):
    """Экспорт заказов в CSV."""
    async with async_session() as session:
        orders, _ = await get_orders_paginated(
            session, status=status, offset=0, limit=10000,
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "avtor24_id", "title", "work_type", "subject", "status",
        "bid_price", "income_rub", "uniqueness_percent", "api_cost_usd",
        "deadline", "created_at",
    ])
    for o in orders:
        writer.writerow([
            o.id, o.avtor24_id, o.title, o.work_type, o.subject, o.status,
            o.bid_price, o.income_rub, o.uniqueness_percent, o.api_cost_usd,
            o.deadline, o.created_at,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"},
    )


# ---------------------------------------------------------------------------
# HTML страницы дашборда + статические файлы
# ---------------------------------------------------------------------------

@router.get("/dashboard/static/{filepath:path}")
async def serve_static(filepath: str):
    """Отдача статических файлов (CSS, JS) без кэширования."""
    file_path = os.path.join(STATIC_DIR, filepath)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        file_path,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/dashboard/login")
async def dashboard_login_page():
    """Страница логина."""
    file_path = os.path.join(STATIC_DIR, "login.html")
    return FileResponse(file_path, media_type="text/html")


@router.get("/dashboard/")
async def dashboard_index(request: Request):
    """Главная страница дашборда (SPA). Редирект на логин если нет токена."""
    token = request.cookies.get("dashboard_token")
    if not token:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard/login")
    from src.dashboard.auth import verify_token
    if not verify_token(token):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard/login")
    file_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(
        file_path, media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _to_msk_iso(dt) -> str:
    """Конвертировать datetime в ISO строку с МСК таймзоной."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt  # Уже строка
    if dt.tzinfo is None:
        # Naive datetime — считаем UTC (от БД) → переводим в МСК
        dt = dt.replace(tzinfo=timezone.utc).astimezone(_MSK)
    return dt.isoformat()


def _order_to_dict(o) -> dict:
    """Конвертировать Order ORM объект в словарь."""
    return {
        "id": o.id,
        "avtor24_id": o.avtor24_id,
        "title": o.title,
        "work_type": o.work_type,
        "subject": o.subject,
        "description": o.description,
        "pages_min": o.pages_min,
        "pages_max": o.pages_max,
        "font_size": o.font_size,
        "line_spacing": o.line_spacing,
        "required_uniqueness": o.required_uniqueness,
        "antiplagiat_system": o.antiplagiat_system,
        "deadline": _to_msk_iso(o.deadline) if o.deadline else None,
        "budget_rub": o.budget_rub,
        "bid_price": o.bid_price,
        "bid_comment": o.bid_comment,
        "bid_placed_at": _to_msk_iso(o.bid_placed_at) if o.bid_placed_at else None,
        "score": o.score,
        "status": o.status,
        "generated_file_path": o.generated_file_path,
        "uniqueness_percent": o.uniqueness_percent,
        "income_rub": o.income_rub,
        "api_cost_usd": round(o.api_cost_usd, 4) if o.api_cost_usd else None,
        "api_tokens_used": o.api_tokens_used,
        "customer_username": o.customer_username,
        "error_message": o.error_message,
        "created_at": _to_msk_iso(o.created_at) if o.created_at else None,
        "updated_at": _to_msk_iso(o.updated_at) if o.updated_at else None,
    }
