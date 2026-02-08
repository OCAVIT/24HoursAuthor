"""Тесты дашборда: healthcheck, WebSocket."""

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health возвращает 200 и status ok."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime" in data
    assert "bot_running" in data
    assert "scheduler_jobs" in data
