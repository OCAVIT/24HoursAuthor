"""Парсинг ленты заказов с Автор24."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)

SEARCH_URL = f"{settings.avtor24_base_url}/order/search"


@dataclass
class OrderSummary:
    """Краткая информация о заказе из ленты."""
    order_id: str
    title: str
    url: str
    work_type: str = ""
    subject: str = ""
    deadline: Optional[str] = None
    time_left: Optional[str] = None
    budget: Optional[int] = None
    bid_count: int = 0
    files_count: int = 0
    customer_online: bool = False
    customer_badge: str = ""
    description_preview: str = ""


def _extract_number(text: str) -> Optional[int]:
    """Извлечь число из строки."""
    match = re.search(r"(\d[\d\s]*)", text.replace(" ", ""))
    if match:
        return int(match.group(1))
    return None


async def parse_order_cards(page: Page) -> list[OrderSummary]:
    """Парсить карточки заказов с текущей страницы."""
    orders = []

    # Ожидаем загрузку карточек
    await page.wait_for_selector(
        ".order-card, .search-result-item, .order-item, [data-order-id]",
        timeout=10000,
    )

    cards = await page.locator(
        ".order-card, .search-result-item, .order-item, [data-order-id]"
    ).all()

    for card in cards:
        try:
            # ID заказа
            order_id = await card.get_attribute("data-order-id") or ""
            if not order_id:
                link = card.locator("a[href*='/order/']")
                href = await link.first.get_attribute("href") if await link.count() > 0 else ""
                if href:
                    match = re.search(r"/order/(\d+)", href)
                    order_id = match.group(1) if match else ""

            if not order_id:
                continue

            # Заголовок
            title_el = card.locator(".order-title, .order-card__title, h3, h2").first
            title = (await title_el.inner_text()).strip() if await card.locator(".order-title, .order-card__title, h3, h2").count() > 0 else ""

            # URL
            link_el = card.locator("a[href*='/order/']").first
            url = await link_el.get_attribute("href") if await card.locator("a[href*='/order/']").count() > 0 else ""
            if url and not url.startswith("http"):
                url = settings.avtor24_base_url + url

            # Тип работы
            work_type_el = card.locator(".order-type, .work-type, .order-card__type")
            work_type = (await work_type_el.first.inner_text()).strip() if await work_type_el.count() > 0 else ""

            # Предмет
            subject_el = card.locator(".order-subject, .subject, .order-card__subject")
            subject = (await subject_el.first.inner_text()).strip() if await subject_el.count() > 0 else ""

            # Дедлайн
            deadline_el = card.locator(".order-deadline, .deadline, .order-card__deadline")
            deadline = (await deadline_el.first.inner_text()).strip() if await deadline_el.count() > 0 else None

            # Осталось времени
            time_left_el = card.locator(".time-left, .order-card__time-left")
            time_left = (await time_left_el.first.inner_text()).strip() if await time_left_el.count() > 0 else None

            # Бюджет
            budget = None
            budget_el = card.locator(".order-price, .price, .order-card__price, .budget")
            if await budget_el.count() > 0:
                budget_text = await budget_el.first.inner_text()
                budget = _extract_number(budget_text)

            # Количество ставок
            bid_count = 0
            bid_el = card.locator(".bid-count, .bids, .order-card__bids")
            if await bid_el.count() > 0:
                bid_text = await bid_el.first.inner_text()
                bid_count = _extract_number(bid_text) or 0

            # Файлы
            files_count = 0
            files_el = card.locator(".files-count, .attachments, .order-card__files")
            if await files_el.count() > 0:
                files_text = await files_el.first.inner_text()
                files_count = _extract_number(files_text) or 0

            # Онлайн ли заказчик
            customer_online = await card.locator(".online, .is-online, .user-online").count() > 0

            # Бейдж заказчика
            badge_el = card.locator(".customer-badge, .badge, .user-badge")
            customer_badge = (await badge_el.first.inner_text()).strip() if await badge_el.count() > 0 else ""

            # Превью описания
            desc_el = card.locator(".order-description, .description, .order-card__desc")
            description_preview = (await desc_el.first.inner_text()).strip() if await desc_el.count() > 0 else ""

            orders.append(OrderSummary(
                order_id=order_id,
                title=title,
                url=url,
                work_type=work_type,
                subject=subject,
                deadline=deadline,
                time_left=time_left,
                budget=budget,
                bid_count=bid_count,
                files_count=files_count,
                customer_online=customer_online,
                customer_badge=customer_badge,
                description_preview=description_preview,
            ))

        except Exception as e:
            logger.warning("Ошибка парсинга карточки заказа: %s", e)
            continue

    return orders


async def fetch_order_list(page: Page, max_pages: int = 3) -> list[OrderSummary]:
    """Получить список заказов с нескольких страниц."""
    all_orders: list[OrderSummary] = []

    for page_num in range(1, max_pages + 1):
        url = f"{SEARCH_URL}?page={page_num}" if page_num > 1 else SEARCH_URL
        logger.info("Парсинг страницы %d: %s", page_num, url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await browser_manager.short_delay()

            orders = await parse_order_cards(page)
            if not orders:
                logger.info("Страница %d пуста, прекращаем парсинг", page_num)
                break

            all_orders.extend(orders)
            logger.info("Страница %d: %d заказов", page_num, len(orders))

            if page_num < max_pages:
                await browser_manager.random_delay(min_sec=2, max_sec=5)

        except Exception as e:
            logger.error("Ошибка парсинга страницы %d: %s", page_num, e)
            break

    logger.info("Итого найдено %d заказов", len(all_orders))
    return all_orders
