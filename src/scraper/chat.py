"""Чтение и отправка сообщений в чат заказчика на Автор24."""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Сообщение из чата."""
    order_id: str
    text: str
    is_incoming: bool  # True = от заказчика, False = от нас
    timestamp: Optional[str] = None


async def get_active_chats(page: Page) -> list[str]:
    """Получить список order_id с новыми сообщениями."""
    try:
        my_orders_url = f"{settings.avtor24_base_url}/cabinet/orders"
        await page.goto(my_orders_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.short_delay()

        # Ищем заказы с непрочитанными сообщениями
        order_ids: list[str] = []
        unread_items = await page.locator(
            ".unread-message, .new-message, .has-new-messages, "
            "[data-unread-count]:not([data-unread-count='0'])"
        ).all()

        for item in unread_items:
            # Ищем ссылку на заказ в родительском элементе
            parent = item.locator("xpath=ancestor::*[contains(@data-order-id, '')]")
            if await parent.count() > 0:
                oid = await parent.first.get_attribute("data-order-id")
                if oid:
                    order_ids.append(oid)
                    continue

            # Или ищем ссылку
            link = item.locator("a[href*='/order/']")
            if await link.count() > 0:
                href = await link.first.get_attribute("href")
                match = re.search(r"/order/(\d+)", href or "")
                if match:
                    order_ids.append(match.group(1))

        logger.info("Найдено %d чатов с новыми сообщениями", len(order_ids))
        return order_ids

    except Exception as e:
        logger.error("Ошибка получения списка чатов: %s", e)
        return []


async def get_messages(page: Page, order_id: str) -> list[ChatMessage]:
    """Получить историю сообщений чата заказа."""
    try:
        chat_url = f"{settings.avtor24_base_url}/order/{order_id}/chat"
        await page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.short_delay()

        messages: list[ChatMessage] = []
        msg_items = await page.locator(
            ".message, .chat-message, .msg-item"
        ).all()

        for msg_el in msg_items:
            try:
                text_el = msg_el.locator(".message-text, .msg-text, .msg-body, p")
                text = (await text_el.first.inner_text()).strip() if await text_el.count() > 0 else ""
                if not text:
                    continue

                # Определяем направление: входящее/исходящее
                classes = await msg_el.get_attribute("class") or ""
                is_incoming = any(
                    kw in classes for kw in ["incoming", "from-customer", "received", "left"]
                )

                # Время
                time_el = msg_el.locator(".message-time, .msg-time, time, .timestamp")
                timestamp = None
                if await time_el.count() > 0:
                    timestamp = (await time_el.first.inner_text()).strip()

                messages.append(ChatMessage(
                    order_id=order_id,
                    text=text,
                    is_incoming=is_incoming,
                    timestamp=timestamp,
                ))
            except Exception:
                continue

        return messages

    except Exception as e:
        logger.error("Ошибка получения сообщений для заказа %s: %s", order_id, e)
        return []


async def send_message(page: Page, order_id: str, text: str) -> bool:
    """Отправить сообщение в чат заказа."""
    try:
        chat_url = f"{settings.avtor24_base_url}/order/{order_id}/chat"
        current = page.url
        if f"/order/{order_id}" not in current:
            await page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
            await browser_manager.short_delay()

        # Поле ввода сообщения
        msg_input = page.locator(
            'textarea[name="message"], textarea.chat-input, '
            '#message-input, .msg-input textarea, textarea[placeholder*="сообщен"]'
        )
        if await msg_input.count() == 0:
            logger.error("Не найдено поле ввода сообщения для заказа %s", order_id)
            return False

        await msg_input.first.fill(text)
        await browser_manager.short_delay()

        # Кнопка отправки
        send_btn = page.locator(
            'button:has-text("Отправить"), button.send-btn, '
            'input[type="submit"], .chat-send-btn'
        )
        if await send_btn.count() > 0:
            await send_btn.first.click()
        else:
            # Отправка по Enter
            await msg_input.first.press("Enter")

        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        await browser_manager.short_delay()

        logger.info("Сообщение отправлено в чат заказа %s", order_id)
        return True

    except Exception as e:
        logger.error("Ошибка отправки сообщения в заказ %s: %s", order_id, e)
        return False


async def send_file_with_message(
    page: Page, order_id: str, filepath: str, message: str
) -> bool:
    """Загрузить файл и отправить сопроводительное сообщение."""
    from pathlib import Path
    from src.scraper.file_handler import upload_file

    file_ok = await upload_file(page, order_id, Path(filepath))
    if not file_ok:
        return False

    msg_ok = await send_message(page, order_id, message)
    return msg_ok
