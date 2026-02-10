"""Диагностика флоу перегенерации: почему файл не отправляется после regen."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from src.database.crud import get_order_by_avtor24_id


async def main():
    order_id = "11941506"

    print("\n" + "="*60)
    print("ДИАГНОСТИКА ПЕРЕГЕНЕРАЦИИ")
    print("="*60 + "\n")

    async with async_session() as session:
        order = await get_order_by_avtor24_id(session, order_id)

    if not order:
        print(f"[ERROR] Заказ #{order_id} не найден")
        return

    print(f"[ORDER ID] {order_id}")
    print(f"[DB ID] {order.id}")
    print(f"[STATUS] {order.status}")
    print(f"[UPDATED] {order.updated_at}")
    print()

    # Проверка статуса
    if order.status == "accepted":
        print("[OK] Статус 'accepted' — заказ в очереди на генерацию")
        print("[INFO] process_accepted_orders_job() подхватит его при следующем запуске (каждые 120 сек)")
        print()
    elif order.status == "ready":
        print("[OK] Статус 'ready' — файл сгенерирован, ждёт доставки")
        print()
        # Проверяем время доставки
        deliver_after_str = order.error_message or ""
        if deliver_after_str:
            try:
                deliver_after = datetime.fromisoformat(deliver_after_str)
                now = datetime.now()
                print(f"[SCHEDULED] Время доставки: {deliver_after.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"[CURRENT] Текущее время: {now.strftime('%Y-%m-%d %H:%M:%S')}")

                if now < deliver_after:
                    remaining_sec = (deliver_after - now).total_seconds()
                    remaining_min = remaining_sec / 60
                    remaining_hours = remaining_min / 60
                    print(f"\n[WARNING] Доставка ОТЛОЖЕНА!")
                    print(f"[WARNING] Осталось: {int(remaining_hours)}ч {int(remaining_min % 60)}м {int(remaining_sec % 60)}с")
                    print(f"\n[TIP] Это задержка для имитации реального времени работы (антибан)")
                    print(f"[TIP] Чтобы отправить СЕЙЧАС:")
                    print(f"      1. Открыть дашборд → Заказы → #{order_id}")
                    print(f"      2. Кликнуть 'Остановить' (сбросит статус)")
                    print(f"      3. Кликнуть 'Перегенерировать'")
                    print(f"      4. В коде main.py найти _calculate_delivery_delay()")
                    print(f"      5. Временно установить return 1  # 1 минута для теста")
                else:
                    print(f"\n[OK] Время доставки наступило!")
                    print(f"[INFO] Файл будет отправлен при следующем запуске process_accepted_orders_job()")
            except (ValueError, TypeError) as e:
                print(f"[ERROR] Некорректное время в error_message: {e}")
        else:
            print("[WARNING] Время доставки не установлено (error_message пуст)")
        print()

    elif order.status == "generating":
        print("[INFO] Статус 'generating' — генерация в процессе")
        print("[TIP] Дождитесь завершения генерации (~2-10 минут)")
        print()
    elif order.status == "delivered":
        print("[OK] Статус 'delivered' — файл УЖЕ отправлен")
        print(f"[INFO] Последнее обновление: {order.updated_at}")
        print()
    else:
        print(f"[WARNING] Неожиданный статус: {order.status}")
        print()

    # Проверка файла
    if order.generated_file_path:
        file_path = Path(order.generated_file_path)
        if file_path.exists():
            size_kb = file_path.stat().st_size / 1024
            print(f"[FILE] {file_path.name} ({size_kb:.1f} KB)")
            print(f"[PATH] {file_path}")
        else:
            print(f"[WARNING] Файл не найден: {file_path}")
    else:
        print("[WARNING] Файл не сгенерирован (generated_file_path пуст)")

    print()
    print("="*60)
    print("РЕКОМЕНДАЦИИ")
    print("="*60)

    if order.status == "ready":
        deliver_after_str = order.error_message or ""
        if deliver_after_str:
            try:
                deliver_after = datetime.fromisoformat(deliver_after_str)
                if datetime.now() < deliver_after:
                    print("\n[ACTION] Файл сгенерирован, но доставка отложена на несколько часов")
                    print("[ACTION] Варианты:")
                    print("  1. ПОДОЖДАТЬ — бот отправит автоматически в назначенное время")
                    print("  2. ОТПРАВИТЬ СЕЙЧАС — запустить scripts/force_delivery.py")
                    print("  3. ОТКЛЮЧИТЬ ЗАДЕРЖКИ — в main.py изменить _calculate_delivery_delay()")
            except:
                pass
    elif order.status == "accepted":
        print("\n[ACTION] Заказ в очереди на генерацию")
        print("[ACTION] Через 1-2 минуты process_accepted_orders_job() начнёт генерацию")
        print("[ACTION] Проверьте логи бота для отслеживания прогресса")
    elif order.status == "delivered":
        print("\n[INFO] Файл уже отправлен, дальнейших действий не требуется")
    else:
        print(f"\n[WARNING] Статус '{order.status}' требует внимания")


if __name__ == "__main__":
    asyncio.run(main())
