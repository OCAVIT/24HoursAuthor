"""Парсинг детальной страницы заказа на Автор24."""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


@dataclass
class OrderDetail:
    """Полная информация о заказе."""
    order_id: str
    title: str
    url: str
    work_type: str = ""
    subject: str = ""
    description: str = ""
    pages_min: Optional[int] = None
    pages_max: Optional[int] = None
    font_size: int = 14
    line_spacing: float = 1.5
    required_uniqueness: Optional[int] = None
    antiplagiat_system: str = ""
    deadline: Optional[str] = None
    budget: Optional[int] = None
    average_bid: Optional[int] = None
    guarantee_days: Optional[int] = None
    customer_info: str = ""
    file_urls: list[str] = field(default_factory=list)


def _parse_int(text: str) -> Optional[int]:
    """Извлечь целое число из строки."""
    match = re.search(r"(\d+)", text.replace(" ", ""))
    return int(match.group(1)) if match else None


def _parse_float(text: str) -> Optional[float]:
    """Извлечь дробное число из строки."""
    match = re.search(r"(\d+[.,]?\d*)", text.replace(" ", ""))
    if match:
        return float(match.group(1).replace(",", "."))
    return None


async def fetch_order_detail(page: Page, order_url: str) -> OrderDetail:
    """Парсинг полной страницы заказа."""
    await page.goto(order_url, wait_until="domcontentloaded", timeout=30000)
    await browser_manager.short_delay()

    # ID из URL
    match = re.search(r"/order/(\d+)", order_url)
    order_id = match.group(1) if match else ""

    # Заголовок
    title = ""
    title_el = page.locator("h1, .order-title, .order-detail__title")
    if await title_el.count() > 0:
        title = (await title_el.first.inner_text()).strip()

    # Полное описание
    description = ""
    desc_el = page.locator(
        ".order-description, .order-detail__description, .task-description, "
        ".order-content, [itemprop='description']"
    )
    if await desc_el.count() > 0:
        description = (await desc_el.first.inner_text()).strip()

    # Блок с параметрами заказа (таблица или dl/dt)
    work_type = ""
    subject = ""
    pages_min = None
    pages_max = None
    font_size = 14
    line_spacing = 1.5
    required_uniqueness = None
    antiplagiat_system = ""
    deadline = None
    budget = None
    average_bid = None
    guarantee_days = None

    # Парсим информационные блоки (dt/dd или label/value)
    info_rows = await page.locator(
        ".order-info__row, .order-param, .info-row, dl dt"
    ).all()

    for row in info_rows:
        try:
            text = (await row.inner_text()).strip().lower()

            if "тип работы" in text or "вид работы" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    work_type = (await val_el.first.inner_text()).strip()

            elif "предмет" in text or "дисциплина" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    subject = (await val_el.first.inner_text()).strip()

            elif "страниц" in text or "объём" in text or "объем" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    numbers = re.findall(r"\d+", val_text)
                    if len(numbers) >= 2:
                        pages_min = int(numbers[0])
                        pages_max = int(numbers[1])
                    elif len(numbers) == 1:
                        pages_min = pages_max = int(numbers[0])

            elif "шрифт" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    size = _parse_int(val_text)
                    if size:
                        font_size = size

            elif "интервал" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    spacing = _parse_float(val_text)
                    if spacing:
                        line_spacing = spacing

            elif "уникальность" in text or "оригинальность" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    required_uniqueness = _parse_int(val_text)

            elif "антиплагиат" in text or "система проверки" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    antiplagiat_system = (await val_el.first.inner_text()).strip()

            elif "срок" in text or "дедлайн" in text or "дата сдачи" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    deadline = (await val_el.first.inner_text()).strip()

            elif "бюджет" in text or "цена" in text or "стоимость" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    budget = _parse_int(val_text)

            elif "средняя ставка" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    average_bid = _parse_int(val_text)

            elif "гарантия" in text or "гарантийный" in text:
                val_el = row.locator("+ dd, .value, .order-info__value")
                if await val_el.count() > 0:
                    val_text = await val_el.first.inner_text()
                    guarantee_days = _parse_int(val_text)

        except Exception:
            continue

    # Альтернативный парсинг типа работы и предмета из хлебных крошек
    if not work_type:
        breadcrumb = page.locator(".breadcrumb, .order-type, .work-type-label")
        if await breadcrumb.count() > 0:
            work_type = (await breadcrumb.first.inner_text()).strip()

    if not subject:
        subj_el = page.locator(".subject-label, .order-subject")
        if await subj_el.count() > 0:
            subject = (await subj_el.first.inner_text()).strip()

    # Бюджет — альтернативный селектор
    if budget is None:
        price_el = page.locator(".order-price, .price-value, .budget-value")
        if await price_el.count() > 0:
            price_text = await price_el.first.inner_text()
            budget = _parse_int(price_text)

    # Информация о заказчике
    customer_info = ""
    cust_el = page.locator(".customer-info, .user-info, .order-customer")
    if await cust_el.count() > 0:
        customer_info = (await cust_el.first.inner_text()).strip()

    # Прикреплённые файлы
    file_urls: list[str] = []
    file_links = await page.locator(
        "a[href*='/download'], a[href*='/file/'], .attachment a, .order-files a"
    ).all()
    for link in file_links:
        href = await link.get_attribute("href")
        if href:
            if not href.startswith("http"):
                href = settings.avtor24_base_url + href
            file_urls.append(href)

    return OrderDetail(
        order_id=order_id,
        title=title,
        url=order_url,
        work_type=work_type,
        subject=subject,
        description=description,
        pages_min=pages_min,
        pages_max=pages_max,
        font_size=font_size,
        line_spacing=line_spacing,
        required_uniqueness=required_uniqueness,
        antiplagiat_system=antiplagiat_system,
        deadline=deadline,
        budget=budget,
        average_bid=average_bid,
        guarantee_days=guarantee_days,
        customer_info=customer_info,
        file_urls=file_urls,
    )
