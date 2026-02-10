"""Проверка реальных цен на странице аукциона заказов.

Цель: понять как отображаются ставки для заказчика и какие цены он видит.
"""

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
    safe_print("\n" + "="*80)
    safe_print("[CHECK] PROVERKA TsEN NA STRANITsE AUKTsIONA")
    safe_print("="*80 + "\n")

    # Авторизация
    safe_print("[AUTH] Avtorizatsiya...")
    page = await login()

    # Переходим на страницу со списком заказов
    safe_print("[NAVIGATE] Perekhod na /home...")
    await page.goto("https://avtor24.ru/home", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Скриншот для проверки
    screenshot_path = Path(__file__).parent / "auction_page.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    safe_print(f"[SCREENSHOT] Сохранён: {screenshot_path}")

    # Парсим все заказы на странице
    safe_print("\n[PARSE] Парсинг заказов в аукционе...")

    orders = await page.evaluate('''() => {
        const cards = document.querySelectorAll('.auctionOrder[data-id]');
        const results = [];

        cards.forEach(card => {
            try {
                const orderId = card.getAttribute('data-id');

                // Заголовок
                const titleEl = card.querySelector('[class*="TitleLinkStyled"]');
                const title = titleEl ? titleEl.textContent.trim() : '';

                // Бюджет заказчика (тот что на карточке)
                const budgetEl = card.querySelector('[class*="BudgetFieldStyled"]');
                let budgetText = budgetEl ? budgetEl.textContent : '';

                // Тип работы
                const typeEl = card.querySelector('.order-info-text');
                const workType = typeEl ? typeEl.textContent.trim() : '';

                // Количество ставок
                const bidsEl = card.querySelector('[class*="CountStyled"]');
                const bidsCount = bidsEl ? parseInt(bidsEl.textContent) : 0;

                // Описание (превью)
                const descEl = card.querySelector('[class*="DescriptionStyled"]');
                const description = descEl ? descEl.textContent.trim().substring(0, 100) : '';

                results.push({
                    orderId,
                    title: title.substring(0, 80),
                    workType,
                    budgetText,
                    bidsCount,
                    description
                });
            } catch (e) {
                console.error('Error parsing card:', e);
            }
        });

        return results;
    }''')

    safe_print(f"\n[FOUND] Найдено {len(orders)} заказов в аукционе:\n")
    safe_print("="*80)

    for i, order in enumerate(orders, 1):
        safe_print(f"\n{i}. ORDER #{order['orderId']}")
        safe_print(f"   Тема: {order['title']}")
        safe_print(f"   Тип: {order['workType']}")
        safe_print(f"   Бюджет на карточке: {order['budgetText']}")
        safe_print(f"   Количество ставок: {order['bidsCount']}")
        safe_print(f"   Описание: {order['description']}...")

    # Теперь выбираем один заказ и смотрим детали + ставки
    if orders:
        test_order = orders[0]
        order_id = test_order['orderId']
        safe_print(f"\n\n{'='*80}")
        safe_print(f"ДЕТАЛИ ЗАКАЗА #{order_id}")
        safe_print("="*80 + "\n")

        # Переходим на страницу заказа
        detail_url = f"https://avtor24.ru/order/getoneorder/{order_id}"
        safe_print(f"[NAVIGATE] {detail_url}")
        await page.goto(detail_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Скриншот детальной страницы
        detail_screenshot = Path(__file__).parent / f"order_{order_id}_detail.png"
        await page.screenshot(path=str(detail_screenshot), full_page=True)
        safe_print(f"[SCREENSHOT] {detail_screenshot}")

        # Парсим детали заказа
        details = await page.evaluate('''() => {
            const result = {};

            // Бюджет заказчика
            const fields = document.querySelectorAll('[class*="FieldStyled"]');
            fields.forEach(field => {
                const children = Array.from(field.children);
                if (children.length === 2) {
                    const label = children[0].textContent.trim();
                    const value = children[1].textContent.trim();

                    if (label.includes('Бюджет')) {
                        result.budget = value;
                    }
                    if (label.includes('Средняя ставка')) {
                        result.averageBid = value;
                    }
                }
            });

            // Ищем блок с ценообразованием (форма ставки)
            const bidInput = document.querySelector('#MakeOffer__inputBid');
            if (bidInput) {
                result.hasBidForm = true;

                // Ищем блок с расчётом цены для заказчика
                const priceInfo = document.querySelector('[class*="PriceInfoDetailedStyled"]');
                if (priceInfo) {
                    result.priceInfoText = priceInfo.textContent;

                    // Ищем конкретные цифры
                    const bElements = priceInfo.querySelectorAll('b');
                    const prices = [];
                    bElements.forEach(b => {
                        prices.push(b.textContent.trim());
                    });
                    result.prices = prices;
                }
            }

            // Смотрим список ставок (если есть)
            const bidsContainer = document.querySelector('[class*="BidsListStyled"]');
            if (bidsContainer) {
                const bidItems = bidsContainer.querySelectorAll('[class*="BidItemStyled"]');
                result.existingBids = [];

                bidItems.forEach(item => {
                    const authorEl = item.querySelector('[class*="AuthorNameStyled"]');
                    const priceEl = item.querySelector('[class*="BidPriceStyled"]');

                    if (authorEl && priceEl) {
                        result.existingBids.push({
                            author: authorEl.textContent.trim(),
                            price: priceEl.textContent.trim()
                        });
                    }
                });
            }

            return result;
        }''')

        safe_print("\n[BUDGET] Бюджет заказчика:", details.get('budget', 'не указан'))
        safe_print("[AVERAGE] Средняя ставка:", details.get('averageBid', 'не указана'))

        if details.get('hasBidForm'):
            safe_print("\n[BID FORM] Форма ставки найдена!")

            if details.get('priceInfoText'):
                safe_print("\n[PRICE INFO] Информация о ценообразовании:")
                safe_print(details['priceInfoText'])

                if details.get('prices'):
                    safe_print("\n[PRICES] Цифры из блока ценообразования:")
                    for price in details['prices']:
                        safe_print(f"  - {price}")

        if details.get('existingBids'):
            safe_print(f"\n[BIDS] Существующие ставки ({len(details['existingBids'])}):")
            for bid in details['existingBids']:
                safe_print(f"  - {bid['author']}: {bid['price']}")

        # Теперь попробуем заполнить форму и посмотреть динамический расчёт
        safe_print("\n\n" + "="*80)
        safe_print("ТЕСТ ДИНАМИЧЕСКОГО РАСЧЁТА ЦЕН")
        safe_print("="*80)

        test_bids = [500, 1000, 2000, 3000, 5000, 10000]

        for test_bid in test_bids:
            try:
                # Вводим тестовую ставку
                await page.fill('#MakeOffer__inputBid', str(test_bid))
                await page.wait_for_timeout(1500)  # Ждём пересчёта

                # Читаем результат
                price_info = await page.evaluate('''() => {
                    const priceDiv = document.querySelector('[class*="PriceInfoDetailedStyled"]');
                    if (!priceDiv) return null;

                    const bElements = priceDiv.querySelectorAll('b');
                    const values = [];
                    bElements.forEach(b => {
                        const text = b.textContent.trim();
                        // Извлекаем число
                        const match = text.match(/[\\d\\s]+/);
                        if (match) {
                            const num = parseInt(match[0].replace(/\\s/g, ''));
                            values.push(num);
                        }
                    });

                    return {
                        text: priceDiv.textContent,
                        values: values
                    };
                }''')

                if price_info and price_info['values'] and len(price_info['values']) >= 2:
                    author_income = price_info['values'][0]
                    customer_pays = price_info['values'][1]
                    multiplier = customer_pays / test_bid if test_bid > 0 else 0

                    safe_print(f"\n  Ставка: {test_bid:6d} RUB")
                    safe_print(f"    → Автор получит:      {author_income:6d} RUB ({author_income/test_bid*100:.1f}%)")
                    safe_print(f"    → Заказчик заплатит:  {customer_pays:6d} RUB (×{multiplier:.2f})")
                    safe_print(f"    → Маржа платформы:    {customer_pays - author_income:6d} RUB")

            except Exception as e:
                safe_print(f"\n  [ERROR] Ставка {test_bid}: {e}")

    await browser_manager.close()
    safe_print("\n\n[DONE] Проверка завершена!")


if __name__ == "__main__":
    asyncio.run(main())
