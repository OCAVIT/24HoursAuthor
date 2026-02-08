"""Генерация событий/уведомлений."""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import create_notification
from src.notifications.websocket import notification_manager


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
        "created_at": str(notification.created_at),
    })
