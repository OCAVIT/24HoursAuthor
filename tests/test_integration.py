"""Интеграционные тесты — полный цикл работы бота.

Проверяет:
1. Заказ → парсинг → скоринг → ставка
2. Принятый заказ → генерация → DOCX → антиплагиат → доставка
3. WebSocket уведомления
4. Dashboard API — все эндпоинты корректно отвечают
"""

import asyncio
import json
import pytest
import pytest_asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from passlib.hash import bcrypt
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.database.models import (
    Base, Order, Notification, ActionLog, DailyStat, BotSetting, Message, ApiUsage,
)
from src.database.crud import (
    create_order, get_order, get_order_by_avtor24_id, update_order_status,
    create_notification, create_action_log, track_api_usage,
    create_message, get_messages_for_order, upsert_daily_stats,
    get_dashboard_stats, get_analytics, get_orders_paginated,
)
from src.scraper.order_detail import OrderDetail
from src.scraper.orders import OrderSummary
from src.analyzer.order_scorer import ScoreResult
from src.generator.essay import GenerationResult
from src.antiplagiat.checker import CheckResult
from src.notifications.websocket import ConnectionManager
from src.main import app


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

TEST_PASSWORD = "integration_test_pass"
TEST_PASSWORD_HASH = bcrypt.hash(TEST_PASSWORD)
TEST_USERNAME = "admin"


@pytest_asyncio.fixture
async def int_engine():
    """Тестовый async engine для интеграционных тестов."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def int_session(int_engine):
    """Тестовая async сессия."""
    factory = async_sessionmaker(int_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest_asyncio.fixture
async def seeded_db(int_session):
    """БД с полным набором тестовых данных для интеграции."""
    # Заказы в разных статусах
    orders_data = [
        {
            "avtor24_id": "10001",
            "title": "Эссе по философии: Смысл жизни по Канту",
            "work_type": "Эссе",
            "subject": "Философия",
            "description": "Напишите эссе 5-7 страниц",
            "pages_min": 5,
            "pages_max": 7,
            "budget_rub": 1500,
            "bid_price": 1350,
            "score": 85,
            "status": "bid_placed",
            "required_uniqueness": 60,
        },
        {
            "avtor24_id": "10002",
            "title": "Курсовая: Анализ финансовой отчётности",
            "work_type": "Курсовая работа",
            "subject": "Экономика",
            "description": "Курсовая работа 30-35 страниц",
            "pages_min": 30,
            "pages_max": 35,
            "budget_rub": 5000,
            "bid_price": 4500,
            "score": 78,
            "status": "accepted",
            "required_uniqueness": 70,
            "antiplagiat_system": "textru",
        },
        {
            "avtor24_id": "10003",
            "title": "Реферат: Великая Отечественная война",
            "work_type": "Реферат",
            "subject": "История",
            "description": "Реферат 15 страниц",
            "pages_min": 15,
            "pages_max": 15,
            "budget_rub": 1200,
            "bid_price": 1080,
            "score": 90,
            "status": "delivered",
            "required_uniqueness": 50,
            "uniqueness_percent": 72.5,
            "income_rub": 1080,
            "api_cost_usd": 0.15,
            "api_tokens_used": 25000,
        },
        {
            "avtor24_id": "10004",
            "title": "Решение задач по высшей математике",
            "work_type": "Контрольная работа",
            "subject": "Математика",
            "description": "10 задач",
            "pages_min": 5,
            "pages_max": 10,
            "budget_rub": 2000,
            "score": 45,
            "status": "rejected",
        },
    ]

    created_orders = []
    for data in orders_data:
        order = Order(**data)
        int_session.add(order)
        await int_session.commit()
        await int_session.refresh(order)
        created_orders.append(order)

    # Сообщения
    await create_message(int_session, order_id=created_orders[0].id, direction="incoming", text="Здравствуйте, сможете сделать?")
    await create_message(int_session, order_id=created_orders[0].id, direction="outgoing", text="Да, тема знакома, смогу в срок.", is_auto_reply=True)

    # Уведомления
    await create_notification(int_session, type="new_order", title="Ставка на эссе", body={"order_id": "10001", "bid_price": 1350}, order_id=created_orders[0].id)
    await create_notification(int_session, type="order_accepted", title="Принят: Курсовая", body={"order_id": "10002"}, order_id=created_orders[1].id)
    await create_notification(int_session, type="order_delivered", title="Отправлено: Реферат", body={"order_id": "10003", "uniqueness": 72.5}, order_id=created_orders[2].id)

    # Логи
    await create_action_log(int_session, action="scan", details="Найдено 8 заказов")
    await create_action_log(int_session, action="score", details="Заказ #10001 — score=85", order_id=created_orders[0].id)
    await create_action_log(int_session, action="bid", details="Заказ #10001 — ставка 1350₽", order_id=created_orders[0].id)
    await create_action_log(int_session, action="generate", details="Реферат сгенерирован: 15 стр", order_id=created_orders[2].id)

    # API usage
    await track_api_usage(int_session, model="gpt-4o-mini", purpose="scoring", input_tokens=500, output_tokens=200, cost_usd=0.0002, order_id=created_orders[0].id)
    await track_api_usage(int_session, model="gpt-4o", purpose="generation", input_tokens=5000, output_tokens=20000, cost_usd=0.21, order_id=created_orders[2].id)

    # Дневная статистика
    today = date.today()
    await upsert_daily_stats(int_session, today, bids_placed=5, orders_accepted=2, orders_delivered=1, income_rub=1080, api_cost_usd=0.36, api_tokens_used=25700)

    # Настройки бота
    from src.database.crud import set_setting
    await set_setting(int_session, "auto_bid", "true")
    await set_setting(int_session, "scan_interval_seconds", "60")

    return created_orders


def _mock_session_factory(session):
    """Создать мок async_session, возвращающий нашу тестовую сессию."""
    class FakeContextManager:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *args):
            pass
    return FakeContextManager


@pytest.fixture
def patch_int_db(int_engine):
    """Подменяем подключение к БД на тестовое."""
    factory = async_sessionmaker(int_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("src.database.connection.async_session", factory), \
         patch("src.dashboard.app.async_session", factory):
        yield factory


@pytest.fixture
def patch_int_auth():
    """Подменяем настройки авторизации."""
    with patch("src.dashboard.auth.settings") as mock_settings:
        mock_settings.dashboard_username = TEST_USERNAME
        mock_settings.dashboard_password_hash = TEST_PASSWORD_HASH
        mock_settings.dashboard_secret_key = "test-secret-key-12345"
        yield mock_settings


async def _get_int_auth_cookie(client: AsyncClient) -> dict:
    """Получить cookie авторизации для интеграционных тестов."""
    response = await client.post(
        "/api/dashboard/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return dict(response.cookies)


# ===========================================================================
# ТЕСТ 1: Полный цикл заказ → парсинг → скоринг → ставка
# ===========================================================================

class TestOrderScanScoreBidCycle:
    """Интеграция: парсинг ленты → скоринг AI → расчёт цены → ставка."""

    async def test_full_scan_score_bid_pipeline(self, int_session):
        """Мок-тест полного пайплайна: HTML → парсинг → скоринг → ставка → БД."""
        # 1. Создаём мок OrderSummary (как будто спарсили из HTML)
        summary = OrderSummary(
            order_id="20001",
            title="Эссе по экономике: Рыночные механизмы",
            url="https://avtor24.ru/order/20001",
            work_type="Эссе",
            subject="Экономика",
            budget=2000,
            bid_count=1,
        )

        # 2. Создаём мок OrderDetail (как будто распарсили страницу заказа)
        detail = OrderDetail(
            order_id="20001",
            title="Эссе по экономике: Рыночные механизмы",
            url="https://avtor24.ru/order/20001",
            work_type="Эссе",
            subject="Экономика",
            description="Написать эссе 5-7 страниц о рыночных механизмах",
            pages_min=5,
            pages_max=7,
            budget=2000,
            required_uniqueness=60,
        )

        # 3. Скоринг — мок GPT-4o-mini
        mock_score = ScoreResult(
            score=82,
            can_do=True,
            estimated_time_min=15,
            estimated_cost_rub=5,
            reason="Эссе по экономике — стандартная задача",
            input_tokens=500,
            output_tokens=150,
            cost_usd=0.0002,
        )

        with patch("src.analyzer.order_scorer.chat_completion_json") as mock_ai:
            mock_ai.return_value = {
                "data": {
                    "score": 82,
                    "can_do": True,
                    "estimated_time_min": 15,
                    "estimated_cost_rub": 5,
                    "reason": "Эссе по экономике — стандартная задача",
                },
                "input_tokens": 500,
                "output_tokens": 150,
                "cost_usd": 0.0002,
            }

            from src.analyzer.order_scorer import score_order
            score_result = await score_order(detail)

        assert score_result.score == 82
        assert score_result.can_do is True

        # 4. Расчёт цены
        from src.analyzer.price_calculator import calculate_price
        price = calculate_price(detail)

        # Бюджет 2000 → ставка 85-95% = 1700-1900
        assert 1700 <= price <= 1900

        # 5. Сохранение в БД
        db_order = await create_order(
            int_session,
            avtor24_id=summary.order_id,
            title=detail.title,
            work_type=detail.work_type,
            subject=detail.subject,
            description=detail.description,
            pages_min=detail.pages_min,
            pages_max=detail.pages_max,
            budget_rub=detail.budget,
            score=score_result.score,
            status="scored",
        )
        assert db_order.id is not None
        assert db_order.status == "scored"

        # 6. Обновляем статус после "ставки"
        updated = await update_order_status(
            int_session, db_order.id, "bid_placed",
            bid_price=price,
            bid_comment="Добрый день! Тема знакома, готов выполнить.",
        )
        assert updated.status == "bid_placed"
        assert updated.bid_price == price

        # 7. Трекинг API usage
        usage = await track_api_usage(
            int_session,
            model="gpt-4o-mini",
            purpose="scoring",
            input_tokens=score_result.input_tokens,
            output_tokens=score_result.output_tokens,
            cost_usd=score_result.cost_usd,
            order_id=db_order.id,
        )
        assert usage.id is not None
        assert usage.order_id == db_order.id

        # 8. Уведомление в БД
        notif = await create_notification(
            int_session,
            type="new_order",
            title=f"Ставка на: {detail.title[:60]}",
            body={
                "order_id": summary.order_id,
                "title": detail.title,
                "work_type": detail.work_type,
                "budget": detail.budget,
                "score": score_result.score,
                "bid_placed": True,
                "bid_price": price,
            },
            order_id=db_order.id,
        )
        assert notif.type == "new_order"
        assert notif.body["bid_placed"] is True

        # 9. Лог действия
        log = await create_action_log(
            int_session,
            action="bid",
            details=f"Заказ #{summary.order_id} — ставка {price}₽",
            order_id=db_order.id,
        )
        assert log.action == "bid"

    async def test_low_score_rejected(self, int_session):
        """Заказ с низким скором не получает ставку."""
        detail = OrderDetail(
            order_id="20002",
            title="Чертёж AutoCAD",
            url="https://avtor24.ru/order/20002",
            work_type="Чертёж",
            subject="Инженерная графика",
            description="Чертёж в AutoCAD",
        )

        with patch("src.analyzer.order_scorer.chat_completion_json") as mock_ai:
            mock_ai.return_value = {
                "data": {
                    "score": 15,
                    "can_do": False,
                    "estimated_time_min": 0,
                    "estimated_cost_rub": 0,
                    "reason": "Чертежи не поддерживаются AI",
                },
                "input_tokens": 400,
                "output_tokens": 100,
                "cost_usd": 0.0001,
            }
            from src.analyzer.order_scorer import score_order
            result = await score_order(detail)

        assert result.score == 15
        assert result.can_do is False

        # Заказ сохраняется с rejected
        db_order = await create_order(
            int_session,
            avtor24_id="20002",
            title=detail.title,
            work_type=detail.work_type,
            subject=detail.subject,
            score=result.score,
            status="rejected",
        )
        assert db_order.status == "rejected"

    async def test_deduplication_prevents_double_processing(self, int_session):
        """Уже обработанный заказ не обрабатывается повторно."""
        await create_order(
            int_session,
            avtor24_id="20003",
            title="Уже обработанный заказ",
            work_type="Эссе",
            status="bid_placed",
        )

        existing = await get_order_by_avtor24_id(int_session, "20003")
        assert existing is not None
        assert existing.status == "bid_placed"

        # Повторный парсинг того же заказа — пропускается
        duplicate = await get_order_by_avtor24_id(int_session, "20003")
        assert duplicate is not None  # Уже есть — не создаём новый


# ===========================================================================
# ТЕСТ 2: Принятый заказ → генерация → DOCX → антиплагиат
# ===========================================================================

class TestGenerationDeliveryCycle:
    """Интеграция: принятый заказ → AI генерация → DOCX → проверка уникальности."""

    async def test_full_generation_pipeline(self, int_session):
        """Полный пайплайн: генерация текста → проверка уникальности → ОК."""
        # Создаём принятый заказ
        order = await create_order(
            int_session,
            avtor24_id="30001",
            title="Эссе по философии: Проблема свободы воли",
            work_type="Эссе",
            subject="Философия",
            description="5-7 страниц, уникальность 60%",
            pages_min=5,
            pages_max=7,
            bid_price=1500,
            status="accepted",
            required_uniqueness=60,
            antiplagiat_system="textru",
        )

        # Мок генерации эссе
        mock_text = "Проблема свободы воли является одной из центральных в философии. " * 100

        with patch("src.generator.essay.chat_completion") as mock_gen, \
             patch("src.antiplagiat.textru.check") as mock_plagiarism:

            mock_gen.return_value = {
                "content": mock_text,
                "model": "gpt-4o",
                "input_tokens": 2000,
                "output_tokens": 8000,
                "total_tokens": 10000,
                "cost_usd": 0.085,
            }

            # Генерация
            from src.generator.essay import generate
            result = await generate(
                title=order.title,
                description=order.description,
                subject=order.subject,
                pages=order.pages_max or 5,
            )

            assert result.text == mock_text
            assert result.pages_approx >= 1
            assert result.cost_usd > 0

            # Мок антиплагиата — уникальность проходит с первого раза
            mock_plagiarism.return_value = 75.3

            from src.antiplagiat.checker import check_uniqueness
            check_result = await check_uniqueness(
                text=result.text,
                system="textru",
                required_uniqueness=60.0,
            )

            assert check_result.uniqueness == 75.3
            assert check_result.is_sufficient is True
            assert check_result.system == "textru"

        # Обновляем заказ в БД
        updated = await update_order_status(
            int_session, order.id, "delivered",
            uniqueness_percent=check_result.uniqueness,
            api_cost_usd=result.cost_usd,
            api_tokens_used=result.total_tokens,
            income_rub=order.bid_price,
        )

        assert updated.status == "delivered"
        assert updated.uniqueness_percent == 75.3
        assert updated.income_rub == 1500

        # Трекинг API
        await track_api_usage(
            int_session,
            model="gpt-4o",
            purpose="generation",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            order_id=order.id,
        )

    async def test_generation_with_rewrite_cycle(self, int_session):
        """Генерация + рерайт когда уникальность недостаточна."""
        order = await create_order(
            int_session,
            avtor24_id="30002",
            title="Реферат: Древний Рим",
            work_type="Реферат",
            subject="История",
            status="accepted",
            required_uniqueness=70,
        )

        original_text = "Древний Рим — одна из величайших цивилизаций. " * 50
        rewritten_text = "Римская империя представляла собой выдающуюся цивилизацию. " * 50

        with patch("src.antiplagiat.textru.check") as mock_check, \
             patch("src.antiplagiat.rewriter.chat_completion") as mock_rewrite:

            # Первая проверка — уникальность 45% (ниже порога)
            mock_check.side_effect = [45.0, 78.0]

            from src.antiplagiat.checker import check_uniqueness

            check1 = await check_uniqueness(text=original_text, system="textru", required_uniqueness=70.0)
            assert check1.uniqueness == 45.0
            assert check1.is_sufficient is False

            # Рерайт
            mock_rewrite.return_value = {
                "content": rewritten_text,
                "model": "gpt-4o",
                "input_tokens": 3000,
                "output_tokens": 3000,
                "total_tokens": 6000,
                "cost_usd": 0.04,
            }

            from src.antiplagiat.rewriter import rewrite_for_uniqueness
            rewrite_result = await rewrite_for_uniqueness(
                text=original_text,
                target_percent=70.0,
                current_percent=45.0,
            )
            assert rewrite_result.text != original_text

            # Повторная проверка — теперь 78%
            check2 = await check_uniqueness(text=rewrite_result.text, system="textru", required_uniqueness=70.0)
            assert check2.uniqueness == 78.0
            assert check2.is_sufficient is True

        # Обновляем заказ
        await update_order_status(int_session, order.id, "delivered", uniqueness_percent=78.0)

    async def test_generate_and_check_integration(self, int_session):
        """Тест generate_and_check из роутера — полный цикл с мок."""
        mock_text = "Текст курсовой работы по экономике. " * 200

        with patch("src.generator.essay.chat_completion") as mock_gen, \
             patch("src.antiplagiat.textru.check") as mock_check:

            mock_gen.return_value = {
                "content": mock_text,
                "model": "gpt-4o",
                "input_tokens": 3000,
                "output_tokens": 10000,
                "total_tokens": 13000,
                "cost_usd": 0.11,
            }
            mock_check.return_value = 72.0

            from src.generator.router import generate_and_check
            gen_result, check_result = await generate_and_check(
                work_type="Эссе",
                title="Тестовое эссе",
                description="Описание",
                subject="Экономика",
                pages=5,
                required_uniqueness=50,
                antiplagiat_system="textru",
            )

            assert gen_result is not None
            assert gen_result.text == mock_text
            assert check_result is not None
            assert check_result.uniqueness == 72.0
            assert check_result.is_sufficient is True

    async def test_unsupported_type_returns_none(self, int_session):
        """Не поддерживаемый тип работы возвращает None."""
        from src.generator.router import generate_and_check
        gen_result, check_result = await generate_and_check(
            work_type="Онлайн-консультация",
            title="Консультация",
        )
        assert gen_result is None
        assert check_result is None


# ===========================================================================
# ТЕСТ 3: WebSocket уведомления
# ===========================================================================

class TestWebSocketNotifications:
    """Интеграция: уведомления → WebSocket → клиент получает."""

    async def test_connection_manager_broadcast(self):
        """Менеджер рассылает сообщение всем подключённым клиентам."""
        manager = ConnectionManager()

        # Мок WebSocket клиент
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_text = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)

        assert len(manager.active_connections) == 2

        # Broadcast уведомления
        notification_data = {
            "type": "new_order",
            "title": "Ставка на эссе",
            "body": {"order_id": "10001", "bid_price": 1350},
        }
        await manager.broadcast(notification_data)

        expected = json.dumps(notification_data, ensure_ascii=False, default=str)
        ws1.send_text.assert_called_once_with(expected)
        ws2.send_text.assert_called_once_with(expected)

    async def test_disconnect_removes_client(self):
        """После отключения клиент не получает broadcast."""
        manager = ConnectionManager()

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()

        await manager.connect(ws)
        assert len(manager.active_connections) == 1

        manager.disconnect(ws)
        assert len(manager.active_connections) == 0

    async def test_broadcast_handles_dead_connection(self):
        """Мёртвое соединение удаляется при broadcast."""
        manager = ConnectionManager()

        ws_alive = AsyncMock()
        ws_alive.accept = AsyncMock()
        ws_alive.send_text = AsyncMock()

        ws_dead = AsyncMock()
        ws_dead.accept = AsyncMock()
        ws_dead.send_text = AsyncMock(side_effect=ConnectionError("closed"))

        await manager.connect(ws_alive)
        await manager.connect(ws_dead)
        assert len(manager.active_connections) == 2

        await manager.broadcast({"type": "test"})

        # Мёртвое соединение удалено
        assert len(manager.active_connections) == 1
        assert ws_alive in manager.active_connections

    async def test_push_notification_saves_to_db_and_broadcasts(self, int_session):
        """push_notification сохраняет в БД и рассылает через WebSocket."""
        mock_broadcast = AsyncMock()

        with patch("src.notifications.events.notification_manager") as mock_manager:
            mock_manager.broadcast = mock_broadcast

            from src.notifications.events import push_notification
            await push_notification(
                int_session,
                type="order_delivered",
                title="Отправлено: Эссе",
                body={"order_id": "10001", "uniqueness": 75.0},
            )

            # Проверяем broadcast был вызван
            mock_broadcast.assert_called_once()
            call_data = mock_broadcast.call_args[0][0]
            assert call_data["type"] == "order_delivered"
            assert call_data["title"] == "Отправлено: Эссе"

        # Проверяем что уведомление сохранено в БД
        from src.database.crud import get_notifications
        notifications = await get_notifications(int_session, limit=10)
        assert len(notifications) >= 1
        latest = notifications[0]
        assert latest.type == "order_delivered"

    async def test_notification_types_complete(self, int_session):
        """Все типы уведомлений из спецификации создаются корректно."""
        types = [
            ("new_order", "Новый заказ", {"order_id": "1", "bid_price": 1000}),
            ("order_accepted", "Заказ принят", {"order_id": "1", "status": "generating"}),
            ("order_delivered", "Работа отправлена", {"order_id": "1", "uniqueness": 72.5}),
            ("new_message", "Новое сообщение", {"order_id": "1", "customer_message": "Привет"}),
            ("error", "Ошибка генерации", {"error": "Таймаут", "requires_attention": True}),
            ("daily_summary", "Сводка за день", {"bids_placed": 5, "income_today": 3000}),
        ]

        for notif_type, title, body in types:
            notif = await create_notification(
                int_session, type=notif_type, title=title, body=body,
            )
            assert notif.type == notif_type
            assert notif.body == body


# ===========================================================================
# ТЕСТ 4: Dashboard API — все эндпоинты
# ===========================================================================

class TestDashboardAPIIntegration:
    """Интеграция: дашборд API — авторизация + все эндпоинты."""

    async def test_health_returns_status(self):
        """GET /health возвращает статус и uptime."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime" in data

    async def test_stats_endpoint(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/stats возвращает виджеты."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_orders" in data
        assert "bids_pending" in data
        assert "income_today" in data
        assert "api_cost_today_usd" in data

    async def test_orders_list_all(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/orders возвращает все заказы."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/orders")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert data["total"] >= 1

    async def test_orders_filter_by_status(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/orders?status=bid_placed фильтрует по статусу."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/orders?status=bid_placed")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["status"] == "bid_placed"

    async def test_order_detail(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/orders/{id} возвращает детали + чат + логи."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            list_resp = await client.get("/api/dashboard/orders")
            items = list_resp.json()["items"]
            assert len(items) >= 1

            order_id = items[0]["id"]
            resp = await client.get(f"/api/dashboard/orders/{order_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "order" in data
        assert "messages" in data
        assert "logs" in data
        assert "api_usage" in data
        assert data["order"]["id"] == order_id

    async def test_order_not_found(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/orders/99999 → 404."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/orders/99999")
        assert resp.status_code == 404

    async def test_stop_order(self, seeded_db, patch_int_db, patch_int_auth):
        """POST /api/dashboard/orders/{id}/stop остановка заказа."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            list_resp = await client.get("/api/dashboard/orders")
            items = list_resp.json()["items"]
            order_id = items[0]["id"]
            resp = await client.post(f"/api/dashboard/orders/{order_id}/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["new_status"] == "rejected"

    async def test_regen_order(self, seeded_db, patch_int_db, patch_int_auth):
        """POST /api/dashboard/orders/{id}/regen перегенерация."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            list_resp = await client.get("/api/dashboard/orders")
            items = list_resp.json()["items"]
            order_id = items[0]["id"]
            resp = await client.post(f"/api/dashboard/orders/{order_id}/regen")
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "accepted"

    async def test_analytics_endpoint(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/analytics возвращает аналитику."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_income_rub" in data
        assert "total_api_cost_usd" in data
        assert "total_tokens" in data
        assert "roi" in data
        assert "daily" in data
        assert "api_by_model" in data
        assert "api_by_purpose" in data

    async def test_notifications_list(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/notifications возвращает уведомления."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "unread_count" in data

    async def test_mark_notifications_read(self, seeded_db, patch_int_db, patch_int_auth):
        """POST /api/dashboard/notifications/read помечает прочитанными."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            notif_resp = await client.get("/api/dashboard/notifications")
            items = notif_resp.json()["items"]
            assert len(items) >= 1

            ids = [items[0]["id"]]
            resp = await client.post(
                "/api/dashboard/notifications/read",
                json={"ids": ids},
            )
        assert resp.status_code == 200
        assert resp.json()["marked"] == 1

    async def test_logs_endpoint(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/logs возвращает логи."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    async def test_settings_get(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/settings возвращает настройки."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "auto_bid" in data
        assert "scan_interval_seconds" in data

    async def test_settings_update(self, seeded_db, patch_int_db, patch_int_auth):
        """PUT /api/dashboard/settings обновляет настройки."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            resp = await client.put(
                "/api/dashboard/settings",
                json={"auto_bid": "false", "scan_interval_seconds": "120"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

            get_resp = await client.get("/api/dashboard/settings")
            data = get_resp.json()
        assert data["auto_bid"] == "false"
        assert data["scan_interval_seconds"] == "120"

    async def test_export_csv(self, seeded_db, patch_int_db, patch_int_auth):
        """GET /api/dashboard/export/csv возвращает CSV."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            resp = await client.get("/api/dashboard/export/csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "id,avtor24_id" in resp.text

    async def test_chat_send(self, seeded_db, patch_int_db, patch_int_auth):
        """POST /api/dashboard/chat/{id}/send отправляет сообщение."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            list_resp = await client.get("/api/dashboard/orders")
            items = list_resp.json()["items"]
            order_id = items[0]["id"]

            resp = await client.post(
                f"/api/dashboard/chat/{order_id}/send",
                json={"text": "Здравствуйте, работа будет готова завтра."},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "message_id" in data

    async def test_chat_send_empty_rejected(self, seeded_db, patch_int_db, patch_int_auth):
        """POST /api/dashboard/chat/{id}/send без текста → 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)

            list_resp = await client.get("/api/dashboard/orders")
            items = list_resp.json()["items"]
            order_id = items[0]["id"]

            resp = await client.post(
                f"/api/dashboard/chat/{order_id}/send",
                json={"text": ""},
            )
        assert resp.status_code == 400

    async def test_unauthorized_without_cookie(self, patch_int_auth):
        """Без авторизации все API возвращают 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            endpoints = [
                "/api/dashboard/stats",
                "/api/dashboard/orders",
                "/api/dashboard/analytics",
                "/api/dashboard/notifications",
                "/api/dashboard/logs",
                "/api/dashboard/settings",
            ]
            for endpoint in endpoints:
                resp = await client.get(endpoint)
                assert resp.status_code == 401, f"{endpoint} should return 401"

    async def test_dashboard_html_pages(self, seeded_db, patch_int_db, patch_int_auth):
        """HTML страницы дашборда доступны."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Страница логина (без авторизации)
            login_resp = await client.get("/dashboard/login")
            assert login_resp.status_code == 200

            # Главная (с авторизацией)
            cookies = await _get_int_auth_cookie(client)
            client.cookies.update(cookies)
            main_resp = await client.get("/dashboard/")
            assert main_resp.status_code == 200


# ===========================================================================
# ТЕСТ 5: Полный сквозной цикл (end-to-end mock)
# ===========================================================================

class TestEndToEndFlow:
    """Сквозной тест: заказ найден → оценён → ставка → принят → сгенерирован → отправлен."""

    async def test_complete_order_lifecycle(self, int_session):
        """Полный жизненный цикл заказа через все статусы."""
        # 1. new → scored
        order = await create_order(
            int_session,
            avtor24_id="50001",
            title="Эссе по социологии: Роль семьи в обществе",
            work_type="Эссе",
            subject="Социология",
            description="Эссе 5 страниц",
            pages_min=5,
            pages_max=5,
            budget_rub=1200,
            status="new",
        )
        assert order.status == "new"

        # 2. scored
        order = await update_order_status(int_session, order.id, "scored", score=80)
        assert order.status == "scored"
        assert order.score == 80

        # 3. bid_placed
        order = await update_order_status(
            int_session, order.id, "bid_placed",
            bid_price=1080,
            bid_comment="Готов выполнить!",
        )
        assert order.status == "bid_placed"
        assert order.bid_price == 1080

        # 4. accepted
        order = await update_order_status(int_session, order.id, "accepted")
        assert order.status == "accepted"

        # 5. generating
        order = await update_order_status(int_session, order.id, "generating")
        assert order.status == "generating"

        # 6. checking_plagiarism
        order = await update_order_status(
            int_session, order.id, "checking_plagiarism",
            uniqueness_percent=68.5,
        )
        assert order.status == "checking_plagiarism"

        # 7. delivered
        order = await update_order_status(
            int_session, order.id, "delivered",
            income_rub=1080,
            api_cost_usd=0.12,
            api_tokens_used=18000,
            generated_file_path="/tmp/essay_50001.docx",
        )
        assert order.status == "delivered"
        assert order.income_rub == 1080
        assert order.api_cost_usd == 0.12

        # 8. completed
        order = await update_order_status(int_session, order.id, "completed")
        assert order.status == "completed"

        # Проверяем что все данные корректны в финальном состоянии
        final = await get_order(int_session, order.id)
        assert final.avtor24_id == "50001"
        assert final.status == "completed"
        assert final.bid_price == 1080
        assert final.income_rub == 1080
        assert final.uniqueness_percent == 68.5

    async def test_order_with_messages_and_logs(self, int_session):
        """Заказ с полной историей: сообщения + логи + API usage."""
        order = await create_order(
            int_session,
            avtor24_id="50002",
            title="Курсовая: Маркетинг",
            work_type="Курсовая работа",
            subject="Маркетинг",
            status="delivered",
            bid_price=4000,
            income_rub=4000,
        )

        # Сообщения
        msg1 = await create_message(int_session, order.id, "incoming", "Когда будет готово?")
        msg2 = await create_message(int_session, order.id, "outgoing", "Завтра к вечеру.", is_auto_reply=True)
        msg3 = await create_message(int_session, order.id, "incoming", "Спасибо!")
        msg4 = await create_message(int_session, order.id, "outgoing", "Работа готова, загружаю.", is_auto_reply=True)

        messages = await get_messages_for_order(int_session, order.id)
        assert len(messages) == 4
        assert messages[0].direction == "incoming"
        assert messages[1].is_auto_reply is True

        # Логи
        log1 = await create_action_log(int_session, "bid", "Ставка 4000₽", order.id)
        log2 = await create_action_log(int_session, "generate", "30 стр, $0.50", order.id)
        log3 = await create_action_log(int_session, "deliver", "Файл отправлен", order.id)

        # API usage
        u1 = await track_api_usage(int_session, "gpt-4o-mini", "scoring", 500, 200, 0.0002, order.id)
        u2 = await track_api_usage(int_session, "gpt-4o", "generation", 10000, 40000, 0.50, order.id)
        u3 = await track_api_usage(int_session, "gpt-4o-mini", "chat", 300, 100, 0.0001, order.id)

        # Уведомления
        n1 = await create_notification(int_session, "new_order", "Ставка", {"bid": 4000}, order.id)
        n2 = await create_notification(int_session, "order_delivered", "Отправлено", {"pages": 30}, order.id)

        # Всё связано с заказом
        assert u1.order_id == order.id
        assert u2.order_id == order.id
        assert n1.order_id == order.id

    async def test_daily_stats_accumulation(self, int_session):
        """Дневная статистика корректно накапливается."""
        today = date.today()

        # Первый заказ за день
        await upsert_daily_stats(int_session, today, bids_placed=1, income_rub=0)
        from src.database.crud import get_daily_stats
        stats = await get_daily_stats(int_session, today)
        assert stats.bids_placed == 1

        # Обновляем — добавляем ещё
        await upsert_daily_stats(int_session, today, bids_placed=3, orders_delivered=1, income_rub=2000)
        stats = await get_daily_stats(int_session, today)
        assert stats.bids_placed == 3
        assert stats.orders_delivered == 1
        assert stats.income_rub == 2000


# ===========================================================================
# ТЕСТ 6: Кросс-модульная интеграция
# ===========================================================================

class TestCrossModuleIntegration:
    """Тесты взаимодействия между разными модулями."""

    async def test_router_maps_all_types_to_generators(self):
        """Все типы работ из маппинга имеют реальные генераторы."""
        from src.generator.router import GENERATORS, is_supported, supported_types

        supported = supported_types()
        assert len(supported) >= 25  # Минимум 25 типов

        # Ключевые типы должны быть поддержаны
        key_types = [
            "Эссе", "Реферат", "Курсовая работа", "Дипломная работа",
            "Контрольная работа", "Решение задач", "Перевод",
            "Бизнес-план", "Отчёт по практике", "Рецензия",
            "Задача по программированию",
        ]
        for wtype in key_types:
            assert is_supported(wtype), f"Тип '{wtype}' должен поддерживаться"

        # Реалтайм типы НЕ поддерживаются
        assert not is_supported("Онлайн-консультация")
        assert not is_supported("Помощь on-line")

    async def test_price_calculator_handles_all_work_types(self):
        """Калькулятор цен даёт результат для всех типов работ."""
        from src.analyzer.price_calculator import calculate_price, BASE_PRICE_PER_PAGE

        for work_type in BASE_PRICE_PER_PAGE:
            detail = OrderDetail(
                order_id="test",
                title="Тест",
                url="https://avtor24.ru/order/test",
                work_type=work_type,
            )
            price = calculate_price(detail)
            assert price >= 300, f"Цена для '{work_type}' должна быть >= 300"

    async def test_ai_client_cost_calculation(self):
        """Расчёт стоимости API корректен для разных моделей."""
        from src.ai_client import calculate_cost

        # GPT-4o: $2.50/1M input, $10.00/1M output
        cost_4o = calculate_cost("gpt-4o", 10000, 5000)
        expected_4o = (10000 / 1_000_000) * 2.50 + (5000 / 1_000_000) * 10.00
        assert abs(cost_4o - expected_4o) < 0.0001

        # GPT-4o-mini: $0.15/1M input, $0.60/1M output
        cost_mini = calculate_cost("gpt-4o-mini", 10000, 5000)
        expected_mini = (10000 / 1_000_000) * 0.15 + (5000 / 1_000_000) * 0.60
        assert abs(cost_mini - expected_mini) < 0.0001

        # gpt-4o-mini дешевле gpt-4o
        assert cost_mini < cost_4o

    async def test_chat_responder_banned_words(self):
        """Чат-респондер содержит список запрещённых слов."""
        from src.chat_ai.responder import BANNED_WORDS

        required_banned = ["ai", "chatgpt", "gpt", "нейросеть", "искусственный интеллект"]
        for word in required_banned:
            assert word in BANNED_WORDS, f"'{word}' должно быть в списке запрещённых"

    async def test_docx_builder_sections_parsing(self):
        """DOCX builder корректно разбивает текст на секции."""
        from src.docgen.builder import _sections_from_text

        text = """Введение

Текст введения.

1. Первая глава

Текст первой главы.

2. Вторая глава

Текст второй главы.

Заключение

Текст заключения.

Список литературы

1. Источник один
2. Источник два"""

        sections = _sections_from_text(text)
        assert len(sections) >= 4  # Введение, 2 главы, заключение, список

        # Проверяем что заголовки извлечены
        headings = [s["heading"] for s in sections]
        has_intro = any("введение" in h.lower() for h in headings)
        has_conclusion = any("заключение" in h.lower() for h in headings)
        assert has_intro
        assert has_conclusion
