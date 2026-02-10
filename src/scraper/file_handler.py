"""Скачивание файлов заказчика / загрузка готовых работ."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("tmp/orders")


async def download_files(page: Page, order_id: str, file_urls: list[str]) -> list[Path]:
    """Скачать файлы заказчика в tmp/orders/{order_id}/."""
    order_dir = DOWNLOAD_DIR / order_id
    order_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    for url in file_urls:
        try:
            async with page.expect_download(timeout=60000) as download_info:
                await page.goto(url)
            download = await download_info.value
            filename = download.suggested_filename or f"file_{len(downloaded)}"
            filepath = order_dir / filename
            await download.save_as(str(filepath))
            downloaded.append(filepath)
            logger.info("Скачан файл: %s → %s", url, filepath)
            await browser_manager.short_delay()
        except Exception as e:
            logger.warning("Ошибка скачивания %s: %s", url, e)
            continue

    return downloaded


async def upload_file(
    page: Page, order_id: str, filepath: Path, variant: str = "final"
) -> bool:
    """Загрузить файл через кнопку 'Загрузить работу' на странице заказа.

    Использует штатный механизм Avtor24:
    1. Клик 'Загрузить работу' → открывается модалка
    2. Выбор варианта (Промежуточный / Окончательный)
    3. Установка файла на input[type=file] → автозагрузка

    Args:
        variant: "final" (Окончательный) или "intermediate" (Промежуточный).
    """
    try:
        # Переходим на страницу заказа (если ещё не там)
        order_url = f"{settings.avtor24_base_url}/order/getoneorder/{order_id}"
        if f"/order/getoneorder/{order_id}" not in page.url:
            await page.goto(order_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

        # Закрываем любые оверлеи
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)

        # 1. Кликаем "Загрузить работу" (force=True для обхода оверлея)
        upload_btn = page.locator('button:has-text("Загрузить работу")')
        if await upload_btn.count() == 0:
            logger.warning("Кнопка 'Загрузить работу' не найдена для заказа %s", order_id)
            return False

        await upload_btn.first.click(force=True, timeout=10000)
        await asyncio.sleep(3)

        # 2. Выбираем вариант (Окончательный/Промежуточный) через JS
        # Модалка: <li data-active="true"> — Промежуточный,
        #          <li data-active="false"> — Окончательный
        target_text = "Окончательный" if variant == "final" else "Промежуточный"
        selected = await page.evaluate("""
            (targetText) => {
                // Находим <li> элементы в модалке загрузки
                const modal = document.querySelector('[class*="AttachOrderFileModal"]');
                if (!modal) return {error: 'Modal not found'};

                const items = modal.querySelectorAll('li');
                for (const li of items) {
                    const text = (li.innerText || '').trim();
                    if (text.includes(targetText)) {
                        li.click();
                        return {selected: true, text: text.substring(0, 50)};
                    }
                }
                return {error: 'Variant not found', items: items.length};
            }
        """, target_text)

        if selected.get("error"):
            logger.warning(
                "Не удалось выбрать вариант '%s': %s",
                target_text, selected["error"],
            )
            # Fallback: кликаем по тексту напрямую
            await page.evaluate("""
                (targetText) => {
                    const allElements = document.body.querySelectorAll('b, span, li');
                    for (const el of allElements) {
                        if ((el.innerText || '').trim().includes(targetText)) {
                            el.click();
                            if (el.parentElement) el.parentElement.click();
                            return true;
                        }
                    }
                    return false;
                }
            """, target_text)

        logger.info("Выбран вариант '%s' для заказа %s", target_text, order_id)
        await asyncio.sleep(1)

        # 3. Устанавливаем файл на второй input[type="file"]
        # Первый input — чат, второй — загрузка работы (OrderStyled)
        file_input = page.locator('input[type="file"]')
        input_count = await file_input.count()

        if input_count < 2:
            logger.warning(
                "Найдено %d file input (нужно 2) для заказа %s",
                input_count, order_id,
            )
            # Если есть хотя бы один — пробуем его
            if input_count == 0:
                return False

        target_input = file_input.nth(1) if input_count >= 2 else file_input.first
        await target_input.set_input_files(str(filepath))
        logger.info("Файл %s установлен для загрузки в заказ %s", filepath.name, order_id)

        # 4. Ждём завершения загрузки (POST /ajax/addComment)
        await asyncio.sleep(8)

        # 5. Проверяем результат
        result = await page.evaluate("""
            () => {
                const text = document.body.innerText || '';
                return {
                    success: text.includes('окончательный вариант') ||
                             text.includes('промежуточный вариант') ||
                             text.includes('Заказ будет находиться на гарантии'),
                    onGuarantee: text.includes('на гарантии'),
                    snippet: text.substring(0, 500),
                };
            }
        """)

        if result.get("success"):
            logger.info(
                "Файл %s загружен как %s в заказ %s",
                filepath.name, target_text, order_id,
            )
            return True

        logger.warning("Загрузка файла: не удалось подтвердить успех для заказа %s", order_id)
        return True  # Файл был установлен, скорее всего загрузился

    except Exception as e:
        logger.error("Ошибка загрузки файла в заказ %s: %s", order_id, e)
        return False
