"""Проверка истории заказа: все действия и изменения статуса."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from sqlalchemy import select
from src.database.models import ActionLog, Order


async def main():
    order_id = "11941506"

    print("\n" + "="*80)
    print(f"ИСТОРИЯ ЗАКАЗА #{order_id}")
    print("="*80 + "\n")

    # Получаем заказ
    async with async_session() as session:
        result = await session.execute(
            select(Order).where(Order.avtor24_id == order_id)
        )
        order = result.scalar_one_or_none()

    if not order:
        print(f"[ERROR] Заказ #{order_id} не найден в БД")
        return

    print(f"[ORDER] #{order_id} (DB ID: {order.id})")
    print(f"[STATUS] {order.status}")
    print(f"[CREATED] {order.created_at}")
    print(f"[UPDATED] {order.updated_at}")
    print()

    # Получаем все действия по этому заказу
    async with async_session() as session:
        result = await session.execute(
            select(ActionLog)
            .where(ActionLog.order_id == order.id)
            .order_by(ActionLog.created_at.asc())
        )
        logs = list(result.scalars().all())

    if not logs:
        print("[WARNING] Нет логов действий для этого заказа")
        return

    print(f"Найдено {len(logs)} действий:\n")
    print("="*80)

    for i, log in enumerate(logs, 1):
        time_str = log.created_at.strftime("%Y-%m-%d %H:%M:%S")
        action_label = {
            "scan": "[SCAN]",
            "score": "[SCORE]",
            "bid": "[BID]",
            "accept": "[ACCEPT]",
            "generate": "[GEN]",
            "plagiarism": "[PLAGIAR]",
            "deliver": "[SEND]",
            "chat": "[CHAT]",
            "error": "[ERROR]",
            "cancel": "[CANCEL]",
            "system": "[SYSTEM]",
        }.get(log.action, f"[{log.action.upper()}]")

        print(f"{i}. {time_str} {action_label}")
        if log.details:
            # Перенос длинных строк
            details = log.details[:200]
            print(f"   {details}")
        print()

    print("="*80)
    print("АНАЛИЗ")
    print("="*80 + "\n")

    # Ищем попытки перегенерации
    regen_attempts = [l for l in logs if "перегенер" in (l.details or "").lower()]
    if regen_attempts:
        print(f"[INFO] Найдено {len(regen_attempts)} попыток перегенерации:")
        for log in regen_attempts:
            print(f"  - {log.created_at}: {log.details[:100]}")
        print()
    else:
        print("[INFO] Попыток перегенерации не обнаружено в логах")
        print("[TIP] Возможно, endpoint /api/dashboard/orders/{id}/regen НЕ записывает логи")
        print()

    # Последние 5 действий
    print("Последние 5 действий:")
    for log in logs[-5:]:
        time_str = log.created_at.strftime("%H:%M:%S")
        print(f"  {time_str} [{log.action}] {(log.details or '')[:80]}")


if __name__ == "__main__":
    asyncio.run(main())
