"""Ручное добавление заказа в БД и запуск генерации.

Используется когда:
1. Заказ принят вручную на сайте (не через бота)
2. Бот по какой-то причине не обнаружил заказ
3. Нужно срочно добавить заказ в систему
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from src.database.crud import create_order, get_order_by_avtor24_id, create_action_log
from src.scraper.auth import login
from src.scraper.order_detail import fetch_order_detail
from src.scraper.browser import browser_manager


async def main():
    order_id = "11941666"

    print("\n" + "="*80)
    print(f"РУЧНОЕ ДОБАВЛЕНИЕ ЗАКАЗА #{order_id}")
    print("="*80 + "\n")

    # 1. Проверяем, нет ли уже в БД
    async with async_session() as session:
        existing = await get_order_by_avtor24_id(session, order_id)

    if existing:
        print(f"[WARNING] Заказ #{order_id} уже есть в БД (ID: {existing.id})")
        print(f"[STATUS] {existing.status}")
        print()
        answer = input("[?] Сбросить статус на 'accepted' для перегенерации? [y/N]: ")
        if answer.lower() == 'y':
            from src.database.crud import update_order_status
            async with async_session() as session:
                await update_order_status(
                    session, existing.id, "accepted",
                    generated_file_path=None, error_message=None,
                )
                await create_action_log(
                    session,
                    action="generate",
                    details=f"Перегенерация запущена вручную через скрипт",
                    order_id=existing.id,
                )
            print("[SUCCESS] Статус сброшен на 'accepted'")
            print("[INFO] process_accepted_orders_job() подхватит заказ через ~2 минуты")
        return

    # 2. Парсим детали заказа с сайта
    print("[AUTH] Авторизация...")
    page = await login()

    print(f"[FETCH] Парсинг деталей заказа #{order_id}...")
    detail_url = f"/order/getoneorder/{order_id}"

    async with browser_manager.page_lock:
        detail = await fetch_order_detail(page, detail_url)

    if not detail:
        print(f"[ERROR] Не удалось спарсить заказ #{order_id}")
        print("[TIP] Проверьте:")
        print("  1. Корректность order_id")
        print("  2. Доступность страницы заказа")
        print("  3. Авторизацию на сайте")
        await browser_manager.close()
        return

    print()
    print(f"[OK] Заказ спарсен:")
    print(f"  Тема: {detail.title}")
    print(f"  Тип: {detail.work_type}")
    print(f"  Предмет: {detail.subject}")
    print(f"  Страниц: {detail.pages_min}-{detail.pages_max}")
    print(f"  Бюджет: {detail.budget_rub} RUB")
    print(f"  Уникальность: {detail.required_uniqueness}% ({detail.antiplagiat_system})")
    print()

    answer = input("[?] Добавить заказ в БД со статусом 'accepted'? [y/N]: ")
    if answer.lower() != 'y':
        print("[CANCELLED]")
        await browser_manager.close()
        return

    # 3. Создаём заказ в БД
    async with async_session() as session:
        order = await create_order(
            session,
            avtor24_id=order_id,
            title=detail.title or f"Заказ #{order_id}",
            work_type=detail.work_type or None,
            subject=detail.subject or None,
            description=detail.description or None,
            pages_min=detail.pages_min,
            pages_max=detail.pages_max,
            font_size=detail.font_size or 14,
            line_spacing=detail.line_spacing or 1.5,
            required_uniqueness=detail.required_uniqueness,
            antiplagiat_system=detail.antiplagiat_system or None,
            budget_rub=detail.budget_rub,
            customer_username=detail.customer_name or None,
            status="accepted",  # Сразу в очередь на генерацию
        )

        await create_action_log(
            session,
            action="accept",
            details=f"Заказ #{order_id} добавлен вручную через скрипт",
            order_id=order.id,
        )

    print(f"\n[SUCCESS] Заказ добавлен в БД!")
    print(f"[DB ID] {order.id}")
    print(f"[STATUS] accepted")
    print()
    print("[INFO] process_accepted_orders_job() подхватит заказ через ~2 минуты")
    print("[INFO] Генерация займёт 5-20 минут в зависимости от типа работы")
    print("[INFO] Доставка будет отложена на реалистичное время (антибан)")

    await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
