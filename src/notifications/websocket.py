"""WebSocket менеджер для реалтайм уведомлений и логов."""

import json
from fastapi import WebSocket


class ConnectionManager:
    """Управление WebSocket соединениями."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Принять новое подключение."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Отключить клиента."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        """Отправить сообщение всем подключённым клиентам."""
        message = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def send_personal(self, websocket: WebSocket, data: dict):
        """Отправить сообщение конкретному клиенту."""
        message = json.dumps(data, ensure_ascii=False, default=str)
        await websocket.send_text(message)


notification_manager = ConnectionManager()
log_manager = ConnectionManager()
