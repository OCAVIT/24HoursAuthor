"""Тест перегенерации и отправки документа для заказа.

Проверяет:
1. Текущий статус заказа в БД
2. Наличие сгенерированного файла
3. Время доставки (если статус ready)
4. Принудительную отправку файла с сообщением
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Добавляем корень проекта в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from src.database.crud import get_order_by_avtor24_id, update_order_status
from src.scraper.auth import login
from src.scraper.chat import send_file_with_message
from src.scraper.browser import browser_manager


async def main():
    order_id = "11941506"

    print(f"\n{'='*60}")
    print(f"ПРОВЕРКА ЗАКАЗА #{order_id}")
    print(f"{'='*60}\n")

    # 1. Получаем заказ из БД
    async with async_session() as session:
        order = await get_order_by_avtor24_id(session, order_id)

    if not order:
        print(f"[ERROR] Заказ #{order_id} не найден в БД")
        return

    print(f"[STATUS] {order.status}")
    print(f"[FILE] {order.generated_file_path}")
    print(f"[BID] {order.bid_price} RUB")
    print(f"[UNIQUENESS] {order.uniqueness_percent}%")
    print(f"[UPDATED] {order.updated_at}")

    # 2. Проверяем время доставки (если статус ready)
    if order.status == "ready":
        deliver_after_str = order.error_message or ""
        if deliver_after_str:
            try:
                deliver_after = datetime.fromisoformat(deliver_after_str)
                now = datetime.now()
                if now < deliver_after:
                    remaining_min = (deliver_after - now).total_seconds() / 60
                    print(f"\n[SCHEDULED] Доставка запланирована на: {deliver_after.strftime('%H:%M:%S')}")
                    print(f"[SCHEDULED] Осталось: ~{remaining_min:.0f} минут")

                    answer = input("\n[?] Отправить файл СЕЙЧАС (минуя задержку)? [y/N]: ")
                    if answer.lower() != 'y':
                        print("[CANCELLED]")
                        return
                else:
                    print(f"\n[OK] Время доставки наступило ({deliver_after.strftime('%H:%M:%S')})")
            except (ValueError, TypeError):
                print(f"\n[WARNING] Некорректное время доставки в error_message: {deliver_after_str}")

    # 3. Проверяем наличие файла
    if not order.generated_file_path:
        print("\n[ERROR] Файл не сгенерирован (generated_file_path пуст)")
        print("[TIP] Запустите перегенерацию через дашборд или установите статус 'accepted'")
        return

    file_path = Path(order.generated_file_path)
    if not file_path.exists():
        print(f"\n[ERROR] Файл не найден: {file_path}")
        print("[TIP] Файл был сгенерирован, но удалён или путь некорректен")
        return

    file_size_kb = file_path.stat().st_size / 1024
    print(f"\n[OK] Файл найден: {file_path.name} ({file_size_kb:.1f} КБ)")

    # 4. Подтверждение отправки
    print(f"\n{'='*60}")
    print("ОТПРАВКА ФАЙЛА")
    print(f"{'='*60}")
    print(f"[ORDER] #{order_id}")
    print(f"[FILE] {file_path.name}")
    print(f"[MESSAGE] 'Добрый день! Работа готова, загружаю файл. Если потребуются правки — пишите, исправлю.'")
    print()

    answer = input("[?] Отправить файл и сообщение? [y/N]: ")
    if answer.lower() != 'y':
        print("[CANCELLED]")
        return

    # 5. Авторизация и отправка
    try:
        print("\n[AUTH] Авторизация...")
        page = await login()

        print(f"[UPLOAD] Отправка файла в заказ #{order_id}...")
        delivery_message = (
            "Добрый день! Работа готова, загружаю файл. "
            "Если потребуются правки — пишите, исправлю."
        )

        async with browser_manager.page_lock:
            send_ok = await send_file_with_message(
                page, order_id, str(file_path), delivery_message,
                variant="final",  # Окончательный вариант
            )

        if send_ok:
            print("[SUCCESS] Файл и сообщение успешно отправлены!")

            # Обновляем статус в БД
            async with async_session() as session:
                await update_order_status(
                    session, order.id, "delivered",
                    error_message=None,  # Очищаем время доставки
                )

            print("[SUCCESS] Статус заказа обновлён на 'delivered'")

            # Создаём запись о сообщении
            from src.database.crud import create_message
            async with async_session() as session:
                await create_message(
                    session,
                    order_id=order.id,
                    direction="outgoing",
                    text=delivery_message,
                    is_auto_reply=True,
                )

            print("[SUCCESS] Сообщение записано в БД")

        else:
            print("[ERROR] Не удалось отправить файл")
            print("[TIP] Проверьте логи Playwright и доступность страницы заказа")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
