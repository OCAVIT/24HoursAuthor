"""CRUD операции для работы с БД."""

from datetime import date, datetime
from typing import Optional
from sqlalchemy import select, update, func
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
