"""Поиск активного заказа в аукционе для тестирования ценообразования."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.auth import login
from src.scraper.browser import browser_manager


def safe_print(text):
    """Print without Unicode errors on Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


async def main():
    safe_print("\n[SEARCH] Finding active order in auction...")

    page = await login()

    # Переходим на страницу поиска заказов
    search_url = "https://avtor24.ru/order/search"
    safe_print(f"[NAVIGATE] {search_url}")
    await page.goto(search_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Скриншот
    screenshot_path = Path(__file__).parent / "search_page.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    safe_print(f"[SCREENSHOT] {screenshot_path}")

    # Парсим первые заказы
    orders = await page.evaluate('''() => {
        const cards = document.querySelectorAll('.auctionOrder[data-id]');
        const results = [];

        for (let i = 0; i < Math.min(5, cards.length); i++) {
            const card = cards[i];
            const orderId = card.getAttribute('data-id');

            const titleEl = card.querySelector('[class*="TitleLinkStyled"]');
            const title = titleEl ? titleEl.textContent.trim() : '';

            const typeEl = card.querySelector('.order-info-text');
            const workType = typeEl ? typeEl.textContent.trim() : '';

            results.push({ orderId, title: title.substring(0, 50), workType });
        }

        return results;
    }''')

    if not orders:
        safe_print("\n[ERROR] No orders found on search page!")
        await browser_manager.close()
        return

    safe_print(f"\n[FOUND] {len(orders)} orders on search page:\n")

    for i, order in enumerate(orders, 1):
        safe_print(f"{i}. #{order['orderId']} - {order['title']} ({order['workType']})")

    # Берём первый заказ для теста
    test_order = orders[0]
    safe_print(f"\n[SELECTED] Testing pricing on order #{test_order['orderId']}")

    # Переходим на страницу заказа
    detail_url = f"https://avtor24.ru/order/getoneorder/{test_order['orderId']}"
    await page.goto(detail_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Проверяем есть ли форма ставки
    bid_form = await page.query_selector('#MakeOffer__inputBid')

    if not bid_form:
        safe_print("[WARNING] No bid form - order may be closed or assigned")
        safe_print("[ACTION] Trying next order...")

        for order in orders[1:]:
            safe_print(f"\n[TRY] Order #{order['orderId']}...")
            detail_url = f"https://avtor24.ru/order/getoneorder/{order['orderId']}"
            await page.goto(detail_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            bid_form = await page.query_selector('#MakeOffer__inputBid')
            if bid_form:
                safe_print(f"[SUCCESS] Found active order: #{order['orderId']}")
                safe_print(f"\nRun: python scripts/test_pricing_formula.py {order['orderId']}")
                await browser_manager.close()
                return

        safe_print("\n[ERROR] No active orders with bid form found in first 5 results")
    else:
        safe_print(f"[SUCCESS] Found active order: #{test_order['orderId']}")
        safe_print(f"\nRun: python scripts/test_pricing_formula.py {test_order['orderId']}")

    await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
