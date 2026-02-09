"""Постановка ставок на заказы Автор24."""

import logging
from datetime import datetime
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


async def place_bid(page: Page, order_url: str, price: int, comment: str) -> bool:
    """Поставить ставку на заказ.

    Args:
        page: Playwright-страница.
        order_url: URL страницы заказа.
        price: Цена ставки в рублях.
        comment: Комментарий к ставке.

    Returns:
        True если ставка успешно поставлена.
    """
    try:
        # Переходим на страницу заказа если ещё не там
        current = page.url
        if order_url not in current:
            await page.goto(order_url, wait_until="domcontentloaded", timeout=30000)
            await browser_manager.short_delay()

        # Заполняем цену (реальный селектор: #MakeOffer__inputBid)
        price_input = page.locator(
            '#MakeOffer__inputBid, input[id*="inputBid"]'
        )
        if await price_input.count() == 0:
            logger.error("Не найдено поле для ввода ставки на %s", order_url)
            return False

        await price_input.first.fill(str(price))
        await browser_manager.short_delay()

        # Заполняем комментарий (реальный селектор: #makeOffer_comment)
        comment_input = page.locator(
            '#makeOffer_comment, textarea[id*="comment"], '
            'textarea[placeholder*="приветствен"]'
        )
        if await comment_input.count() > 0:
            await comment_input.first.fill(comment)
            await browser_manager.short_delay()

        # Нажимаем кнопку "Поставить ставку"
        submit_btn = page.locator(
            'button:has-text("Поставить ставку")'
        )
        if await submit_btn.count() == 0:
            logger.error("Не найдена кнопка отправки ставки на %s", order_url)
            return False

        await submit_btn.first.click()

        # Ждём подтверждения (редирект или появление сообщения об успехе)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        await browser_manager.short_delay()

        # Проверяем успешность (ищем сообщение об ошибке)
        error_el = page.locator(".error-message, .alert-danger, .bid-error")
        if await error_el.count() > 0:
            error_text = await error_el.first.inner_text()
            logger.error("Ошибка при постановке ставки: %s", error_text)
            return False

        logger.info("Ставка %d₽ поставлена на %s", price, order_url)
        return True

    except Exception as e:
        logger.error("Ошибка постановки ставки на %s: %s", order_url, e)
        return False
