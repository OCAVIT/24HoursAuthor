"""Тесты дашборда: healthcheck, авторизация, API эндпоинты."""

import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from passlib.hash import bcrypt

from src.main import app
from src.database.models import Base, Order, Notification, ActionLog, DailyStat, BotSetting
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


TEST_PASSWORD = "testpass123"
TEST_PASSWORD_HASH = bcrypt.hash(TEST_PASSWORD)
TEST_USERNAME = "admin"


@pytest_asyncio.fixture
async def test_engine():
    """Тестовый async engine (SQLite in-memory)."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine):
    """Тестовая async сессия."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest_asyncio.fixture
async def seeded_session(test_session):
    """Сессия с тестовыми данными."""
    # Создаём тестовый заказ
    order = Order(
        avtor24_id="99001",
        title="Тестовая курсовая работа",
        work_type="Курсовая работа",
        subject="Экономика",
        description="Тестовое описание заказа",
        pages_min=20,
        pages_max=30,
        budget_rub=3000,
        bid_price=2800,
        score=85,
        status="bid_placed",
    )
    test_session.add(order)
    await test_session.commit()
    await test_session.refresh(order)

    # Уведомление
    notif = Notification(
        type="new_order",
        title="Тестовое уведомление",
        body={"order_id": "99001", "bid_price": 2800},
        order_id=order.id,
    )
    test_session.add(notif)

    # Лог
    log = ActionLog(action="bid", details="Ставка 2800₽", order_id=order.id)
    test_session.add(log)

    await test_session.commit()
    yield test_session


@pytest.fixture
def patch_db(test_engine):
    """Подменяем подключение к БД на тестовое."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("src.database.connection.async_session", factory), \
         patch("src.dashboard.app.async_session", factory):
        yield factory


@pytest.fixture
def patch_auth():
    """Подменяем настройки авторизации."""
    with patch("src.dashboard.auth.settings") as mock_settings:
        mock_settings.dashboard_username = TEST_USERNAME
        mock_settings.dashboard_password_hash = TEST_PASSWORD_HASH
        mock_settings.dashboard_secret_key = "test-secret-key-12345"
        yield mock_settings


async def _get_auth_cookie(client: AsyncClient, patch_auth_fixture) -> dict:
    """Получить cookie авторизации."""
    response = await client.post(
        "/api/dashboard/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return dict(response.cookies)


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success(patch_auth):
    """Правильный пароль → 200 + cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/dashboard/login",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["username"] == TEST_USERNAME
    assert "dashboard_token" in response.cookies


@pytest.mark.asyncio
async def test_login_fail(patch_auth):
    """Неправильный пароль → 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/dashboard/login",
            json={"username": TEST_USERNAME, "password": "wrongpassword"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_username(patch_auth):
    """Неправильный логин → 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/dashboard/login",
            json={"username": "hacker", "password": TEST_PASSWORD},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unauthorized(patch_auth):
    """Без cookie → 401 на защищённом эндпоинте."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/dashboard/stats")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token(patch_auth):
    """Невалидный токен → 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("dashboard_token", "invalid-token-value")
        response = await client.get("/api/dashboard/stats")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/stats → JSON с нужными полями."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/stats")

    assert response.status_code == 200
    data = response.json()
    assert "active_orders" in data
    assert "bids_pending" in data
    assert "income_today" in data
    assert "income_week" in data
    assert "income_total" in data
    assert "api_cost_today_usd" in data
    assert "bot_running" in data
    assert "uptime" in data


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orders_list(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/orders → пагинированный список."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/orders")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "per_page" in data
    assert "pages" in data
    assert data["total"] >= 1
    assert len(data["items"]) >= 1

    order = data["items"][0]
    assert "id" in order
    assert "avtor24_id" in order
    assert "title" in order
    assert "status" in order


@pytest.mark.asyncio
async def test_orders_list_filter_status(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/orders?status=bid_placed → фильтрация по статусу."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/orders?status=bid_placed")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["status"] == "bid_placed"


@pytest.mark.asyncio
async def test_orders_list_empty_filter(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/orders?status=completed → пустой список если нет."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/orders?status=completed")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_order_detail(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/orders/1 → детали заказа."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/orders/1")

    assert response.status_code == 200
    data = response.json()
    assert "order" in data
    assert "messages" in data
    assert "logs" in data
    assert "api_usage" in data
    assert data["order"]["avtor24_id"] == "99001"


@pytest.mark.asyncio
async def test_order_not_found(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/orders/9999 → 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/orders/9999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stop_order(seeded_session, patch_db, patch_auth):
    """POST /api/dashboard/orders/1/stop → остановка заказа."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.post("/api/dashboard/orders/1/stop")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["new_status"] == "rejected"


@pytest.mark.asyncio
async def test_regen_order(seeded_session, patch_db, patch_auth):
    """POST /api/dashboard/orders/1/regen → перегенерация."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.post("/api/dashboard/orders/1/regen")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["new_status"] == "accepted"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notifications_list(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/notifications → список уведомлений."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/notifications")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "unread_count" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_mark_notifications_read(seeded_session, patch_db, patch_auth):
    """POST /api/dashboard/notifications/read → пометить прочитанными."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.post(
            "/api/dashboard/notifications/read",
            json={"ids": [1]},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["marked"] == 1


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logs_list(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/logs → логи действий."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/logs")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_settings(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/settings → настройки с дефолтами."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/settings")

    assert response.status_code == 200
    data = response.json()
    assert "auto_bid" in data
    assert "scan_interval_seconds" in data
    assert "max_concurrent_orders" in data


@pytest.mark.asyncio
async def test_update_settings(seeded_session, patch_db, patch_auth):
    """PUT /api/dashboard/settings → обновление настроек."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.put(
            "/api/dashboard/settings",
            json={"scan_interval_seconds": "90", "auto_bid": "false"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analytics(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/analytics → аналитика."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/analytics")

    assert response.status_code == 200
    data = response.json()
    assert "total_income_rub" in data
    assert "total_api_cost_usd" in data
    assert "total_tokens" in data
    assert "roi" in data
    assert "daily" in data
    assert "api_by_model" in data
    assert "api_by_purpose" in data


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_csv(seeded_session, patch_db, patch_auth):
    """GET /api/dashboard/export/csv → CSV файл."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.get("/api/dashboard/export/csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "99001" in response.text


# ---------------------------------------------------------------------------
# Chat send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_send(seeded_session, patch_db, patch_auth):
    """POST /api/dashboard/chat/1/send → ручное сообщение."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.post(
            "/api/dashboard/chat/1/send",
            json={"text": "Тестовое сообщение от владельца"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "message_id" in data


@pytest.mark.asyncio
async def test_chat_send_empty(seeded_session, patch_db, patch_auth):
    """POST /api/dashboard/chat/1/send с пустым текстом → 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies = await _get_auth_cookie(client, patch_auth)
        client.cookies.update(cookies)
        response = await client.post(
            "/api/dashboard/chat/1/send",
            json={"text": ""},
        )

    assert response.status_code == 400
