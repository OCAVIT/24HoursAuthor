"""Генерация событий/уведомлений."""

from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import create_notification
from src.notifications.websocket import notification_manager

_MSK = timezone(timedelta(hours=3))


def _to_msk_iso(dt) -> str:
    """Конвертировать datetime в ISO строку с МСК таймзоной."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(_MSK)
    return dt.isoformat()


async def push_notification(
    session: AsyncSession,
    type: str,
    title: str,
    body: dict,
    order_id: Optional[int] = None,
) -> None:
    """Сохранить уведомление в БД и отправить через WebSocket."""
    notification = await create_notification(
        session, type=type, title=title, body=body, order_id=order_id,
    )
    await notification_manager.broadcast({
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "order_id": notification.order_id,
        "created_at": _to_msk_iso(notification.created_at),
    })
