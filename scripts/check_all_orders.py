"""Проверка всех заказов в работе (не delivered/completed)."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from sqlalchemy import select
from src.database.models import Order


async def main():
    print("\n" + "="*80)
    print("ВСЕ ЗАКАЗЫ В РАБОТЕ")
    print("="*80 + "\n")

    async with async_session() as session:
        # Все заказы кроме completed/rejected/skipped/cancelled
        result = await session.execute(
            select(Order)
            .where(Order.status.notin_(["completed", "rejected", "skipped", "cancelled"]))
            .order_by(Order.updated_at.desc())
        )
        orders = list(result.scalars().all())

    if not orders:
        print("[INFO] Нет заказов в работе")
        return

    print(f"Найдено {len(orders)} заказов:\n")

    for i, order in enumerate(orders, 1):
        status_emoji = {
            "new": "[NEW]",
            "scored": "[SCORED]",
            "bid_placed": "[BID]",
            "accepted": "[QUEUE]",  # В очереди на генерацию
            "generating": "[GEN...]",
            "checking_plagiarism": "[CHECK]",
            "rewriting": "[REWRITE]",
            "ready": "[READY]",  # Сгенерирован, ждёт отправки
            "delivered": "[SENT]",
        }.get(order.status, f"[{order.status.upper()}]")

        print(f"{i}. {status_emoji} #{order.avtor24_id}")
        print(f"   Тема: {order.title[:60]}...")
        print(f"   Статус: {order.status}")
        print(f"   Обновлён: {order.updated_at}")

        # Для accepted/generating/ready — показать дополнительную информацию
        if order.status == "accepted":
            print(f"   [INFO] В очереди на генерацию (process_accepted_orders_job)")
        elif order.status == "generating":
            print(f"   [INFO] Генерация в процессе...")
        elif order.status == "ready":
            deliver_after_str = order.error_message or ""
            if deliver_after_str:
                try:
                    deliver_after = datetime.fromisoformat(deliver_after_str)
                    now = datetime.now()
                    if now < deliver_after:
                        remaining_min = (deliver_after - now).total_seconds() / 60
                        print(f"   [SCHEDULED] Доставка через ~{int(remaining_min)} минут ({deliver_after.strftime('%H:%M:%S')})")
                    else:
                        print(f"   [READY] Время доставки наступило! Будет отправлен при следующем цикле")
                except:
                    pass
        elif order.status == "delivered":
            if order.generated_file_path:
                file_path = Path(order.generated_file_path)
                if file_path.exists():
                    size_kb = file_path.stat().st_size / 1024
                    print(f"   [FILE] {file_path.name} ({size_kb:.1f} KB)")

        if order.error_message and order.status != "ready":
            print(f"   [ERROR] {order.error_message[:100]}")

        print()

    print("="*80)
    print("ИТОГО")
    print("="*80)

    status_counts = {}
    for order in orders:
        status_counts[order.status] = status_counts.get(order.status, 0) + 1

    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    # Проблемные заказы
    print("\n" + "="*80)
    print("ТРЕБУЮТ ВНИМАНИЯ")
    print("="*80 + "\n")

    accepted = [o for o in orders if o.status == "accepted"]
    ready = [o for o in orders if o.status == "ready"]
    errors = [o for o in orders if o.status == "error"]

    if accepted:
        print(f"[QUEUE] {len(accepted)} заказов в очереди на генерацию:")
        for o in accepted:
            print(f"  - #{o.avtor24_id}: {o.title[:50]}...")
        print()

    if ready:
        print(f"[READY] {len(ready)} заказов готовы к отправке:")
        for o in ready:
            deliver_after_str = o.error_message or ""
            status_text = "ждёт времени доставки"
            if deliver_after_str:
                try:
                    deliver_after = datetime.fromisoformat(deliver_after_str)
                    if datetime.now() >= deliver_after:
                        status_text = "ГОТОВ К ОТПРАВКЕ СЕЙЧАС!"
                except:
                    pass
            print(f"  - #{o.avtor24_id}: {o.title[:50]}... ({status_text})")
        print()

    if errors:
        print(f"[ERROR] {len(errors)} заказов с ошибками:")
        for o in errors:
            print(f"  - #{o.avtor24_id}: {o.error_message[:80]}")
        print()

    if not accepted and not ready and not errors:
        print("[OK] Нет заказов, требующих внимания")


if __name__ == "__main__":
    asyncio.run(main())
