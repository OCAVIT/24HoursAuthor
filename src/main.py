"""FastAPI приложение — точка входа."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from src.notifications.websocket import notification_manager, log_manager

start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения."""
    yield


app = FastAPI(title="Avtor24 Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    """Healthcheck эндпоинт."""
    uptime = int(time.time() - start_time)
    return {"status": "ok", "uptime": uptime}


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
