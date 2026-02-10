"""Принудительная генерация и немедленная отправка (без задержек антибана)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import async_session
from src.database.crud import (
    get_order_by_avtor24_id, update_order_status, create_message, create_action_log
)
from src.generator.router import generate_and_check
from src.docgen.builder import build_docx
from src.scraper.auth import login
from src.scraper.chat import send_file_with_message
from src.scraper.browser import browser_manager


async def main():
    order_id = sys.argv[1] if len(sys.argv) > 1 else "11941666"

    print(f"\n{'='*80}")
    print(f"ПРИНУДИТЕЛЬНАЯ ГЕНЕРАЦИЯ И ОТПРАВКА: #{order_id}")
    print(f"{'='*80}\n")

    # 1. Получаем заказ
    async with async_session() as session:
        order = await get_order_by_avtor24_id(session, order_id)

    if not order:
        print(f"[ERROR] Заказ #{order_id} не найден в БД")
        return

    print(f"[ORDER] #{order_id} (DB ID: {order.id})")
    print(f"[STATUS] {order.status}")
    print(f"[TITLE] {order.title}")
    print(f"[WORK_TYPE] {order.work_type}")
    print(f"[PAGES] {order.pages_max or order.pages_min or 10}")
    print()

    # 2. Генерация
    if order.status in ("accepted", "error", "generating"):
        print("[GENERATE] Запуск генерации...")
        async with async_session() as session:
            await update_order_status(session, order.id, "generating")

        try:
            gen_result, check_result = await generate_and_check(
                work_type=order.work_type or "Реферат",
                title=order.title,
                description=order.description or "",
                subject=order.subject or "",
                pages=order.pages_max or order.pages_min or 10,
                required_uniqueness=order.required_uniqueness or 50,
                font_size=order.font_size or 14,
                line_spacing=order.line_spacing or 1.5,
                antiplagiat_system=order.antiplagiat_system or "textru",
            )

            if not gen_result:
                print("[ERROR] Генерация не удалась")
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "error",
                        error_message="Генерация не удалась",
                    )
                return

            uniqueness = check_result.uniqueness if check_result else 0.0
            print(f"[GENERATE] Завершено: ~{gen_result.pages_approx} стр, ${gen_result.cost_usd:.2f}")
            print(f"[PLAGIARISM] Уникальность: {uniqueness:.1f}%")

            # Сборка DOCX
            print("[DOCX] Сборка файла...")
            docx_path = await build_docx(
                title=order.title,
                text=gen_result.text,
                work_type=order.work_type or "Реферат",
                subject=order.subject or "",
                font_size=order.font_size or 14,
                line_spacing=order.line_spacing or 1.5,
            )

            if not docx_path:
                print("[ERROR] Не удалось собрать DOCX")
                async with async_session() as session:
                    await update_order_status(
                        session, order.id, "error",
                        error_message="Не удалось собрать DOCX",
                    )
                return

            size_kb = Path(docx_path).stat().st_size / 1024
            print(f"[DOCX] Готов: {Path(docx_path).name} ({size_kb:.1f} KB)")

            # Обновляем статус на ready
            async with async_session() as session:
                await update_order_status(
                    session, order.id, "ready",
                    generated_file_path=str(docx_path),
                    uniqueness_percent=uniqueness,
                    api_cost_usd=gen_result.cost_usd,
                    api_tokens_used=gen_result.total_tokens,
                )

        except Exception as e:
            print(f"[ERROR] Ошибка генерации: {e}")
            import traceback
            traceback.print_exc()
            async with async_session() as session:
                await update_order_status(
                    session, order.id, "error",
                    error_message=str(e)[:500],
                )
            return

    # 3. Отправка (независимо от времени)
    async with async_session() as session:
        order = await get_order_by_avtor24_id(session, order_id)

    if not order.generated_file_path:
        print("[ERROR] Файл не сгенерирован")
        return

    file_path = Path(order.generated_file_path)
    if not file_path.exists():
        print(f"[ERROR] Файл не найден: {file_path}")
        return

    print(f"\n[SEND] Отправка файла в чат #{order_id}...")
    delivery_message = (
        "Добрый день! Работа готова, загружаю файл. "
        "Если потребуются правки — пишите, исправлю."
    )

    page = await login()
    async with browser_manager.page_lock:
        send_ok = await send_file_with_message(
            page, order_id, str(file_path), delivery_message,
            variant="final",
        )

    if send_ok:
        print("[SUCCESS] Файл и сообщение успешно отправлены!")

        async with async_session() as session:
            await update_order_status(
                session, order.id, "delivered",
                error_message=None,
            )

            await create_message(
                session,
                order_id=order.id,
                direction="outgoing",
                text=delivery_message,
                is_auto_reply=True,
            )

            await create_action_log(
                session,
                action="deliver",
                details=f"Файл отправлен принудительно через force_generate_and_send.py",
                order_id=order.id,
            )

        print("[SUCCESS] Статус обновлён на 'delivered'")
    else:
        print("[ERROR] Не удалось отправить файл")

    await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
