"""Проверка сообщений в чате заказа на Avtor24."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.auth import login
from src.scraper.chat import get_messages
from src.scraper.browser import browser_manager


async def main():
    order_id = "11941506"

    print(f"\n{'='*60}")
    print(f"ПРОВЕРКА ЧАТА ЗАКАЗА #{order_id}")
    print(f"{'='*60}\n")

    try:
        print("[AUTH] Авторизация...")
        page = await login()

        print(f"[FETCH] Получение сообщений из чата #{order_id}...")
        messages = await get_messages(page, order_id)

        if not messages:
            print("[WARNING] Сообщений не найдено")
            return

        print(f"\n[OK] Найдено {len(messages)} сообщений:\n")

        for i, msg in enumerate(messages, 1):
            direction = "OUT" if not msg.is_incoming else "IN"
            if msg.is_system:
                direction = "SYS"
            elif msg.is_assistant:
                direction = "AST"

            text_preview = msg.text[:100].replace('\n', ' ')
            files_info = f" [FILES: {len(msg.file_urls)}]" if msg.has_files else ""

            print(f"{i}. [{direction}]{files_info} {text_preview}")

        # Последние 3 сообщения — подробно
        print(f"\n{'='*60}")
        print("ПОСЛЕДНИЕ 3 СООБЩЕНИЯ (подробно):")
        print(f"{'='*60}\n")

        for msg in messages[-3:]:
            direction = "ИСХОДЯЩЕЕ (наше)" if not msg.is_incoming else "ВХОДЯЩЕЕ (заказчик)"
            if msg.is_system:
                direction = "СИСТЕМНОЕ"
            elif msg.is_assistant:
                direction = "АССИСТЕНТ"

            print(f"{'='*40}")
            print(f"Направление: {direction}")
            if msg.timestamp:
                print(f"Время: {msg.timestamp}")
            if msg.has_files:
                print(f"Файлы ({len(msg.file_urls)}):")
                for url in msg.file_urls:
                    print(f"  - {url}")
            print(f"Текст:\n{msg.text}")
            print()

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
