"""Тесты БД: модели, CRUD операции."""

import pytest
from datetime import date

from src.database.models import Base, Order, Notification, ActionLog, ApiUsage, BotSetting, DailyStat, Message
from src.database.crud import (
    create_order, get_order, get_order_by_avtor24_id, update_order_status,
    create_notification, get_notifications, mark_notifications_read,
    create_action_log, track_api_usage, get_daily_stats, upsert_daily_stats,
    create_message, get_messages_for_order,
    get_setting, set_setting,
)


@pytest.mark.asyncio
async def test_tables_created(engine):
    """Все таблицы создаются без ошибок."""
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: sync_conn.dialect.get_table_names(sync_conn)
        )
    expected = {"orders", "messages", "action_logs", "daily_stats", "notifications", "bot_settings", "api_usage"}
    assert expected.issubset(set(tables))


@pytest.mark.asyncio
async def test_create_and_get_order(session):
    """Создание и получение заказа."""
    order = await create_order(
        session,
        avtor24_id="12345",
        title="Курсовая по экономике",
        work_type="Курсовая работа",
        subject="Экономика",
        budget_rub=3000,
    )
    assert order.id is not None
    assert order.avtor24_id == "12345"
    assert order.status == "new"

    fetched = await get_order(session, order.id)
    assert fetched is not None
    assert fetched.title == "Курсовая по экономике"


@pytest.mark.asyncio
async def test_get_order_by_avtor24_id(session):
    """Получение заказа по avtor24_id."""
    await create_order(session, avtor24_id="99999", title="Тестовый заказ")
    order = await get_order_by_avtor24_id(session, "99999")
    assert order is not None
    assert order.title == "Тестовый заказ"


@pytest.mark.asyncio
async def test_update_order_status(session):
    """Обновление статуса заказа."""
    order = await create_order(session, avtor24_id="11111", title="Тест статус")
    updated = await update_order_status(session, order.id, "bid_placed", bid_price=2500)
    assert updated.status == "bid_placed"
    assert updated.bid_price == 2500


@pytest.mark.asyncio
async def test_create_notification(session):
    """Создание и получение уведомлений."""
    order = await create_order(session, avtor24_id="22222", title="Для уведомления")
    notif = await create_notification(
        session,
        type="new_order",
        title="Новый заказ",
        body={"order_id": "22222", "budget": 3000},
        order_id=order.id,
    )
    assert notif.id is not None
    assert notif.is_read is False

    notifs = await get_notifications(session, unread_only=True)
    assert len(notifs) >= 1


@pytest.mark.asyncio
async def test_mark_notifications_read(session):
    """Пометка уведомлений как прочитанных."""
    notif = await create_notification(
        session, type="error", title="Ошибка", body={"error": "тест"},
    )
    assert notif.is_read is False

    await mark_notifications_read(session, [notif.id])
    notifs = await get_notifications(session, unread_only=True)
    read_ids = [n.id for n in notifs]
    assert notif.id not in read_ids


@pytest.mark.asyncio
async def test_action_log(session):
    """Запись лога действия."""
    log = await create_action_log(session, action="scan", details="Найдено 5 заказов")
    assert log.id is not None
    assert log.action == "scan"


@pytest.mark.asyncio
async def test_api_usage(session):
    """Трекинг использования API."""
    usage = await track_api_usage(
        session,
        model="gpt-4o",
        purpose="generation",
        input_tokens=1000,
        output_tokens=2000,
        cost_usd=0.05,
    )
    assert usage.id is not None
    assert usage.cost_usd == 0.05


@pytest.mark.asyncio
async def test_daily_stats(session):
    """Создание и обновление дневной статистики."""
    today = date.today()
    stat = await upsert_daily_stats(session, today, bids_placed=5, income_rub=3000)
    assert stat.bids_placed == 5

    stat2 = await upsert_daily_stats(session, today, bids_placed=7)
    assert stat2.bids_placed == 7
    assert stat2.id == stat.id


@pytest.mark.asyncio
async def test_messages(session):
    """Создание и получение сообщений чата."""
    order = await create_order(session, avtor24_id="33333", title="Чат тест")
    msg = await create_message(session, order.id, "incoming", "Когда будет готово?")
    assert msg.id is not None
    assert msg.direction == "incoming"

    msgs = await get_messages_for_order(session, order.id)
    assert len(msgs) == 1
    assert msgs[0].text == "Когда будет готово?"


@pytest.mark.asyncio
async def test_bot_settings(session):
    """Настройки бота: запись и чтение."""
    await set_setting(session, "auto_bid", "true")
    val = await get_setting(session, "auto_bid")
    assert val == "true"

    await set_setting(session, "auto_bid", "false")
    val2 = await get_setting(session, "auto_bid")
    assert val2 == "false"


@pytest.mark.asyncio
async def test_nonexistent_order(session):
    """Несуществующий заказ возвращает None."""
    order = await get_order(session, 999999)
    assert order is None
