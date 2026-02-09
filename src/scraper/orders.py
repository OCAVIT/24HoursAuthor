"""Парсинг ленты заказов с Автор24 (React SPA)."""

import logging
import re
from dataclasses import dataclass, field
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
    budget: Optional[str] = None
    budget_rub: Optional[int] = None
    bid_count: int = 0
    files_info: str = ""
    customer_name: str = ""
    customer_online: str = ""
    customer_badges: list[str] = field(default_factory=list)
    description_preview: str = ""
    creation_time: str = ""


def _extract_number(text: str) -> Optional[int]:
    """Извлечь число из строки вида '6 000₽' или '4 ставки'."""
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else None


async def parse_order_cards(page: Page) -> list[OrderSummary]:
    """Парсить карточки заказов с текущей страницы (React SPA).

    Сайт рендерит карточки через React в div#root.
    Каждая карточка: .auctionOrder с data-id.
    """
    orders = []

    # Ожидаем загрузку React-компонентов
    try:
        await page.wait_for_selector(".auctionOrder", timeout=15000)
    except Exception:
        logger.warning("Карточки .auctionOrder не появились за 15 сек")
        return orders

    # Извлекаем данные через JS (быстрее, чем множественные Playwright-запросы)
    raw_orders = await page.evaluate("""
        () => {
            let cards = document.querySelectorAll('.auctionOrder');
            return Array.from(cards).map(card => {
                let orderId = card.getAttribute('data-id') || '';

                // Заголовок
                let titleEl = card.querySelector('[class*="TitleLinkStyled"] span');
                let title = titleEl ? titleEl.textContent.trim() : '';

                // URL
                let linkEl = card.querySelector('a[href*="/order/getoneorder/"]');
                let url = linkEl ? linkEl.getAttribute('href') : '';

                // Информационные поля (.order-info-text)
                let infoTexts = Array.from(card.querySelectorAll('.order-info-text')).map(
                    el => el.textContent.trim()
                );
                // Порядок: [тип работы, дедлайн, предмет, файлы]
                let workType = infoTexts[0] || '';
                let deadline = infoTexts[1] || '';
                let subject = infoTexts[2] || '';
                let filesInfo = infoTexts[3] || '';

                // Описание
                let descEl = card.querySelector('[class*="DescriptionStyled"]');
                let description = descEl ? descEl.textContent.trim() : '';

                // Бюджет
                let budgetEl = card.querySelector('[class*="OrderBudgetStyled"]');
                let budget = budgetEl ? budgetEl.textContent.trim() : '';

                // Ставки
                let offersEl = card.querySelector('[class*="OffersStyled"]');
                let offersText = offersEl ? offersEl.textContent.trim() : '';
                let bidsMatch = offersText.match(/(\\d+)\\s*став/);
                let bidCount = bidsMatch ? parseInt(bidsMatch[1]) : 0;

                // Время создания
                let timeEl = card.querySelector('.orderCreation');
                let creationTime = timeEl ? timeEl.textContent.trim() : '';

                // Онлайн-статус заказчика
                let onlineEl = card.querySelector('[class*="CustomerOnlineStyled"]');
                let customerOnline = onlineEl ? onlineEl.textContent.trim() : '';

                // Имя заказчика
                let customerNameEl = card.querySelector('[class*="CustomerStyled"] a, [class*="customer"] a[href*="/user/"], a[href*="/user/"]');
                let customerName = customerNameEl ? customerNameEl.textContent.trim() : '';
                // Fallback: ищем текст рядом с "Заказчик" или в блоке CustomerStyled
                if (!customerName) {
                    let custBlock = card.querySelector('[class*="CustomerStyled"], [class*="customer"]');
                    if (custBlock) {
                        let lines = custBlock.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
                        customerName = lines.find(t =>
                            t !== 'Заказчик' && !t.includes('онлайн') && !t.includes('назад')
                            && !t.includes('сейчас') && t.length > 1
                        ) || '';
                    }
                }

                // Бейджи (Постоянный клиент, и т.д.)
                let badgeEls = card.querySelectorAll('[class*="customer_label"], [class*="Badges"] b');
                let badges = Array.from(badgeEls).map(el => el.textContent.trim()).filter(Boolean);

                return {
                    orderId, title, url, workType, deadline, subject,
                    filesInfo, description, budget, bidCount,
                    creationTime, customerOnline, customerName, badges,
                };
            });
        }
    """)

    for raw in raw_orders:
        if not raw["orderId"]:
            continue

        budget_rub = _extract_number(raw["budget"]) if raw["budget"] else None

        orders.append(OrderSummary(
            order_id=raw["orderId"],
            title=raw["title"],
            url=raw["url"],
            work_type=raw["workType"],
            subject=raw["subject"],
            deadline=raw["deadline"] or None,
            budget=raw["budget"],
            budget_rub=budget_rub,
            bid_count=raw["bidCount"],
            files_info=raw["filesInfo"],
            customer_name=raw.get("customerName", ""),
            customer_online=raw["customerOnline"],
            customer_badges=raw["badges"],
            description_preview=raw["description"],
            creation_time=raw["creationTime"],
        ))

    return orders


async def fetch_order_list(page: Page, max_pages: int = 3) -> list[OrderSummary]:
    """Получить список заказов с нескольких страниц."""
    all_orders: list[OrderSummary] = []

    for page_num in range(1, max_pages + 1):
        url = f"{SEARCH_URL}?page={page_num}" if page_num > 1 else SEARCH_URL
        logger.info("Парсинг страницы %d: %s", page_num, url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # React SPA — ждём рендеринга
            await browser_manager.short_delay()
            await page.wait_for_selector(".auctionOrder", timeout=15000)
            # Дополнительная задержка для полного рендера
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
