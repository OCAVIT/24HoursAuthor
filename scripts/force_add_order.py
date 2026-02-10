"""Принудительное добавление заказа в БД и запуск генерации (БЕЗ подтверждений)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from src.database.crud import create_order, get_order_by_avtor24_id, update_order_status, create_action_log
from src.scraper.auth import login
from src.scraper.order_detail import fetch_order_detail
from src.scraper.browser import browser_manager


async def main():
    order_id = sys.argv[1] if len(sys.argv) > 1 else "11941666"

    print(f"\n[START] Принудительное добавление заказа #{order_id}\n")

    # 1. Проверяем БД
    async with async_session() as session:
        existing = await get_order_by_avtor24_id(session, order_id)

    if existing:
        print(f"[INFO] Заказ #{order_id} уже в БД (ID: {existing.id}, status: {existing.status})")
        if existing.status != "accepted":
            print(f"[ACTION] Сброс статуса {existing.status} → accepted")
            async with async_session() as session:
                await update_order_status(
                    session, existing.id, "accepted",
                    generated_file_path=None, error_message=None,
                )
                await create_action_log(
                    session,
                    action="generate",
                    details="Перегенерация: сброс статуса через force_add_order.py",
                    order_id=existing.id,
                )
            print(f"[SUCCESS] Статус обновлён на 'accepted'")
        else:
            print("[INFO] Статус уже 'accepted' — заказ в очереди на генерацию")
        return

    # 2. Парсим заказ
    print("[AUTH] Авторизация...")
    page = await login()

    print(f"[FETCH] Парсинг #{order_id}...")
    detail_url = f"/order/getoneorder/{order_id}"

    async with browser_manager.page_lock:
        detail = await fetch_order_detail(page, detail_url)

    # 3. Создаём заказ (даже если поля пустые)
    title = (detail.title or f"Заказ #{order_id}") if detail else f"Заказ #{order_id}"
    work_type = detail.work_type if detail else "Реферат"  # дефолт
    pages_max = detail.pages_max if detail and detail.pages_max else 10  # дефолт

    print(f"\n[PARSED]")
    print(f"  Тема: {title}")
    print(f"  Тип: {work_type}")
    print(f"  Страниц: {pages_max}")
    print()

    async with async_session() as session:
        order = await create_order(
            session,
            avtor24_id=order_id,
            title=title,
            work_type=work_type,
            subject=detail.subject if detail else None,
            description=detail.description if detail else None,
            pages_min=detail.pages_min if detail else None,
            pages_max=pages_max,
            font_size=detail.font_size if detail else 14,
            line_spacing=detail.line_spacing if detail else 1.5,
            required_uniqueness=detail.required_uniqueness if detail else 50,
            antiplagiat_system=detail.antiplagiat_system if detail else "textru",
            budget_rub=detail.budget_rub if detail else None,
            customer_username=detail.customer_name if detail else None,
            status="accepted",
        )

        await create_action_log(
            session,
            action="accept",
            details=f"Заказ #{order_id} добавлен принудительно через force_add_order.py",
            order_id=order.id,
        )

    print(f"[SUCCESS] Заказ добавлен в БД (DB ID: {order.id})")
    print(f"[STATUS] accepted")
    print(f"\n[INFO] process_accepted_orders_job() подхватит заказ через ~2 минуты")

    await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
