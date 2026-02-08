"""CRUD операции для работы с БД."""

from datetime import date, datetime
from typing import Optional
from sqlalchemy import select, update, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Order, Message, ActionLog, DailyStat,
    Notification, BotSetting, ApiUsage,
)


# --- Orders ---

async def create_order(session: AsyncSession, **kwargs) -> Order:
    """Создать заказ."""
    order = Order(**kwargs)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_order(session: AsyncSession, order_id: int) -> Optional[Order]:
    """Получить заказ по ID."""
    result = await session.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one_or_none()


async def get_order_by_avtor24_id(session: AsyncSession, avtor24_id: str) -> Optional[Order]:
    """Получить заказ по avtor24_id."""
    result = await session.execute(select(Order).where(Order.avtor24_id == avtor24_id))
    return result.scalar_one_or_none()


async def update_order_status(session: AsyncSession, order_id: int, status: str, **kwargs) -> Optional[Order]:
    """Обновить статус заказа."""
    stmt = (
        update(Order)
        .where(Order.id == order_id)
        .values(status=status, updated_at=func.now(), **kwargs)
    )
    await session.execute(stmt)
    await session.commit()
    return await get_order(session, order_id)


async def get_orders_by_status(session: AsyncSession, status: str) -> list[Order]:
    """Получить заказы по статусу."""
    result = await session.execute(select(Order).where(Order.status == status))
    return list(result.scalars().all())


# --- Notifications ---

async def create_notification(
    session: AsyncSession,
    type: str,
    title: str,
    body: dict,
    order_id: Optional[int] = None,
) -> Notification:
    """Создать уведомление."""
    notification = Notification(
        type=type, title=title, body=body, order_id=order_id,
    )
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    return notification


async def get_notifications(
    session: AsyncSession,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    """Получить уведомления."""
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_notifications_read(session: AsyncSession, ids: list[int]) -> None:
    """Пометить уведомления как прочитанные."""
    stmt = (
        update(Notification)
        .where(Notification.id.in_(ids))
        .values(is_read=True)
    )
    await session.execute(stmt)
    await session.commit()


# --- Action Logs ---

async def create_action_log(
    session: AsyncSession,
    action: str,
    details: str = "",
    order_id: Optional[int] = None,
) -> ActionLog:
    """Записать лог действия."""
    log = ActionLog(action=action, details=details, order_id=order_id)
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log


# --- API Usage ---

async def track_api_usage(
    session: AsyncSession,
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    order_id: Optional[int] = None,
) -> ApiUsage:
    """Записать использование API."""
    usage = ApiUsage(
        order_id=order_id,
        model=model,
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    session.add(usage)
    await session.commit()
    await session.refresh(usage)
    return usage


# --- Daily Stats ---

async def get_daily_stats(session: AsyncSession, target_date: date) -> Optional[DailyStat]:
    """Получить статистику за день."""
    result = await session.execute(
        select(DailyStat).where(DailyStat.date == target_date)
    )
    return result.scalar_one_or_none()


async def upsert_daily_stats(session: AsyncSession, target_date: date, **kwargs) -> DailyStat:
    """Обновить или создать статистику за день."""
    stat = await get_daily_stats(session, target_date)
    if stat is None:
        stat = DailyStat(date=target_date, **kwargs)
        session.add(stat)
    else:
        for key, value in kwargs.items():
            setattr(stat, key, value)
    await session.commit()
    await session.refresh(stat)
    return stat


# --- Messages ---

async def create_message(
    session: AsyncSession,
    order_id: int,
    direction: str,
    text: str,
    is_auto_reply: bool = False,
) -> Message:
    """Создать сообщение чата."""
    msg = Message(
        order_id=order_id,
        direction=direction,
        text=text,
        is_auto_reply=is_auto_reply,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def get_messages_for_order(session: AsyncSession, order_id: int) -> list[Message]:
    """Получить все сообщения для заказа."""
    result = await session.execute(
        select(Message)
        .where(Message.order_id == order_id)
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


# --- Bot Settings ---

async def get_setting(session: AsyncSession, key: str) -> Optional[str]:
    """Получить настройку бота."""
    result = await session.execute(
        select(BotSetting).where(BotSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting(session: AsyncSession, key: str, value: str) -> BotSetting:
    """Установить настройку бота."""
    result = await session.execute(
        select(BotSetting).where(BotSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = BotSetting(key=key, value=value)
        session.add(setting)
    else:
        setting.value = value
    await session.commit()
    await session.refresh(setting)
    return setting


async def get_all_settings(session: AsyncSession) -> dict[str, str]:
    """Получить все настройки бота как словарь."""
    result = await session.execute(select(BotSetting))
    settings_list = result.scalars().all()
    return {s.key: s.value for s in settings_list}


async def set_many_settings(session: AsyncSession, data: dict[str, str]) -> None:
    """Обновить несколько настроек за один раз."""
    for key, value in data.items():
        existing = await session.execute(
            select(BotSetting).where(BotSetting.key == key)
        )
        setting = existing.scalar_one_or_none()
        if setting is None:
            session.add(BotSetting(key=key, value=str(value)))
        else:
            setting.value = str(value)
    await session.commit()


# --- Dashboard queries ---

async def get_orders_paginated(
    session: AsyncSession,
    status: Optional[str] = None,
    work_type: Optional[str] = None,
    subject: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Order], int]:
    """Получить заказы с фильтрацией, сортировкой и пагинацией."""
    stmt = select(Order)
    count_stmt = select(func.count(Order.id))

    if status:
        stmt = stmt.where(Order.status == status)
        count_stmt = count_stmt.where(Order.status == status)
    if work_type:
        stmt = stmt.where(Order.work_type == work_type)
        count_stmt = count_stmt.where(Order.work_type == work_type)
    if subject:
        stmt = stmt.where(Order.subject == subject)
        count_stmt = count_stmt.where(Order.subject == subject)
    if min_price is not None:
        stmt = stmt.where(Order.bid_price >= min_price)
        count_stmt = count_stmt.where(Order.bid_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Order.bid_price <= max_price)
        count_stmt = count_stmt.where(Order.bid_price <= max_price)

    # Сортировка
    sort_column = getattr(Order, sort_by, Order.created_at)
    order_func = desc if sort_dir == "desc" else asc
    stmt = stmt.order_by(order_func(sort_column))

    stmt = stmt.offset(offset).limit(limit)

    total_result = await session.execute(count_stmt)
    total = total_result.scalar() or 0
    result = await session.execute(stmt)
    orders = list(result.scalars().all())
    return orders, total


async def get_dashboard_stats(session: AsyncSession) -> dict:
    """Получить сводную статистику для виджетов дашборда."""
    today = date.today()

    # Активные заказы (не завершённые и не отклонённые)
    active_result = await session.execute(
        select(func.count(Order.id)).where(
            Order.status.notin_(["completed", "rejected", "new", "scored"])
        )
    )
    active_count = active_result.scalar() or 0

    # Ставки в ожидании
    bids_result = await session.execute(
        select(func.count(Order.id)).where(Order.status == "bid_placed")
    )
    bids_pending = bids_result.scalar() or 0

    # Доход за сегодня
    today_stats = await get_daily_stats(session, today)

    # Доход за неделю
    week_start = date.today().toordinal() - date.today().weekday()
    week_date = date.fromordinal(week_start)
    week_result = await session.execute(
        select(func.coalesce(func.sum(DailyStat.income_rub), 0)).where(
            DailyStat.date >= week_date
        )
    )
    income_week = week_result.scalar() or 0

    # Доход за всё время
    total_income_result = await session.execute(
        select(func.coalesce(func.sum(DailyStat.income_rub), 0))
    )
    income_total = total_income_result.scalar() or 0

    # API расход за сегодня
    today_api_result = await session.execute(
        select(func.coalesce(func.sum(ApiUsage.cost_usd), 0)).where(
            func.date(ApiUsage.created_at) == today
        )
    )
    api_cost_today = today_api_result.scalar() or 0

    return {
        "active_orders": active_count,
        "bids_pending": bids_pending,
        "income_today": today_stats.income_rub if today_stats else 0,
        "income_week": income_week,
        "income_total": income_total,
        "api_cost_today_usd": round(float(api_cost_today), 4),
        "orders_delivered_today": today_stats.orders_delivered if today_stats else 0,
        "bids_placed_today": today_stats.bids_placed if today_stats else 0,
    }


async def get_analytics(
    session: AsyncSession,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Аналитика за период: доход, заказы, токены, ROI."""
    ds_stmt = select(DailyStat)
    if date_from:
        ds_stmt = ds_stmt.where(DailyStat.date >= date_from)
    if date_to:
        ds_stmt = ds_stmt.where(DailyStat.date <= date_to)
    ds_stmt = ds_stmt.order_by(asc(DailyStat.date))
    ds_result = await session.execute(ds_stmt)
    daily_rows = list(ds_result.scalars().all())

    total_income = sum(d.income_rub or 0 for d in daily_rows)
    total_api_cost = sum(d.api_cost_usd or 0 for d in daily_rows)
    total_tokens = sum(d.api_tokens_used or 0 for d in daily_rows)
    total_bids = sum(d.bids_placed or 0 for d in daily_rows)
    total_accepted = sum(d.orders_accepted or 0 for d in daily_rows)
    total_delivered = sum(d.orders_delivered or 0 for d in daily_rows)

    daily_data = [
        {
            "date": str(d.date),
            "income_rub": d.income_rub or 0,
            "api_cost_usd": round(d.api_cost_usd or 0, 4),
            "bids_placed": d.bids_placed or 0,
            "orders_accepted": d.orders_accepted or 0,
            "orders_delivered": d.orders_delivered or 0,
            "api_tokens_used": d.api_tokens_used or 0,
        }
        for d in daily_rows
    ]

    # API usage по моделям
    api_stmt = select(
        ApiUsage.model,
        func.sum(ApiUsage.input_tokens).label("input_tokens"),
        func.sum(ApiUsage.output_tokens).label("output_tokens"),
        func.sum(ApiUsage.cost_usd).label("cost_usd"),
    ).group_by(ApiUsage.model)
    if date_from:
        api_stmt = api_stmt.where(func.date(ApiUsage.created_at) >= date_from)
    if date_to:
        api_stmt = api_stmt.where(func.date(ApiUsage.created_at) <= date_to)
    api_result = await session.execute(api_stmt)
    api_by_model = [
        {
            "model": row.model,
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "cost_usd": round(float(row.cost_usd or 0), 4),
        }
        for row in api_result.all()
    ]

    # API usage по назначению
    purpose_stmt = select(
        ApiUsage.purpose,
        func.sum(ApiUsage.cost_usd).label("cost_usd"),
        func.sum(ApiUsage.input_tokens + ApiUsage.output_tokens).label("total_tokens"),
    ).group_by(ApiUsage.purpose)
    if date_from:
        purpose_stmt = purpose_stmt.where(func.date(ApiUsage.created_at) >= date_from)
    if date_to:
        purpose_stmt = purpose_stmt.where(func.date(ApiUsage.created_at) <= date_to)
    purpose_result = await session.execute(purpose_stmt)
    api_by_purpose = [
        {
            "purpose": row.purpose,
            "cost_usd": round(float(row.cost_usd or 0), 4),
            "total_tokens": row.total_tokens or 0,
        }
        for row in purpose_result.all()
    ]

    roi = round(total_income / (total_api_cost * 90) if total_api_cost > 0 else 0, 1)

    return {
        "total_income_rub": total_income,
        "total_api_cost_usd": round(total_api_cost, 4),
        "total_tokens": total_tokens,
        "total_bids": total_bids,
        "total_accepted": total_accepted,
        "total_delivered": total_delivered,
        "acceptance_rate": round(total_accepted / total_bids * 100, 1) if total_bids else 0,
        "roi": roi,
        "daily": daily_data,
        "api_by_model": api_by_model,
        "api_by_purpose": api_by_purpose,
    }


async def get_action_logs_paginated(
    session: AsyncSession,
    action_filter: Optional[str] = None,
    search: Optional[str] = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[ActionLog], int]:
    """Получить логи действий с фильтрацией и пагинацией."""
    stmt = select(ActionLog)
    count_stmt = select(func.count(ActionLog.id))

    if action_filter:
        stmt = stmt.where(ActionLog.action == action_filter)
        count_stmt = count_stmt.where(ActionLog.action == action_filter)
    if search:
        stmt = stmt.where(ActionLog.details.contains(search))
        count_stmt = count_stmt.where(ActionLog.details.contains(search))

    stmt = stmt.order_by(desc(ActionLog.created_at)).offset(offset).limit(limit)

    total_result = await session.execute(count_stmt)
    total = total_result.scalar() or 0
    result = await session.execute(stmt)
    logs = list(result.scalars().all())
    return logs, total


async def get_notifications_paginated(
    session: AsyncSession,
    type_filter: Optional[str] = None,
    unread_only: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Notification], int]:
    """Получить уведомления с фильтрацией и пагинацией."""
    stmt = select(Notification)
    count_stmt = select(func.count(Notification.id))

    if type_filter:
        stmt = stmt.where(Notification.type == type_filter)
        count_stmt = count_stmt.where(Notification.type == type_filter)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)
        count_stmt = count_stmt.where(Notification.is_read == False)

    stmt = stmt.order_by(desc(Notification.created_at)).offset(offset).limit(limit)

    total_result = await session.execute(count_stmt)
    total = total_result.scalar() or 0
    result = await session.execute(stmt)
    notifications = list(result.scalars().all())
    return notifications, total


async def get_unread_notification_count(session: AsyncSession) -> int:
    """Количество непрочитанных уведомлений."""
    result = await session.execute(
        select(func.count(Notification.id)).where(Notification.is_read == False)
    )
    return result.scalar() or 0
