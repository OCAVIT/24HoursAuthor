"""Скачивание файлов заказчика / загрузка готовых работ."""

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


async def upload_file(page: Page, order_id: str, filepath: Path) -> bool:
    """Загрузить файл в чат заказчика."""
    try:
        # Переходим в чат заказа
        chat_url = f"{settings.avtor24_base_url}/order/{order_id}/chat"
        await page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.short_delay()

        # Ищем input для файла
        file_input = page.locator('input[type="file"]')
        if await file_input.count() == 0:
            # Возможно, нужно нажать на кнопку "Прикрепить файл"
            attach_btn = page.locator(
                '.attach-btn, .file-attach, button:has-text("Прикрепить"), '
                'button:has-text("Файл"), .upload-btn'
            )
            if await attach_btn.count() > 0:
                await attach_btn.first.click()
                await browser_manager.short_delay()

        file_input = page.locator('input[type="file"]')
        if await file_input.count() > 0:
            await file_input.first.set_input_files(str(filepath))
            logger.info("Файл %s прикреплён к заказу %s", filepath.name, order_id)
            await browser_manager.short_delay()
            return True

        logger.warning("Не найден input для загрузки файла в заказе %s", order_id)
        return False

    except Exception as e:
        logger.error("Ошибка загрузки файла в заказ %s: %s", order_id, e)
        return False
