"""Тест формулы ценообразования на реальном заказе.

Заходим на любой заказ, заполняем разные ставки и смотрим:
1. Сколько получит автор (%)
2. Сколько заплатит заказчик (множитель)
3. Зависимость множителя от суммы
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
    # Тестовый заказ (можно заменить на любой другой ID)
    test_order_id = sys.argv[1] if len(sys.argv) > 1 else "11941666"

    safe_print("\n" + "="*80)
    safe_print(f"[TEST] PRICING FORMULA TEST - ORDER #{test_order_id}")
    safe_print("="*80 + "\n")

    # Авторизация
    safe_print("[AUTH] Login...")
    page = await login()

    # Переходим на страницу заказа
    detail_url = f"https://avtor24.ru/order/getoneorder/{test_order_id}"
    safe_print(f"[NAVIGATE] {detail_url}")
    await page.goto(detail_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Скриншот
    screenshot_path = Path(__file__).parent / f"pricing_test_{test_order_id}.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    safe_print(f"[SCREENSHOT] {screenshot_path}\n")

    # Проверяем есть ли форма ставки
    bid_input = await page.query_selector('#MakeOffer__inputBid')
    if not bid_input:
        safe_print("[ERROR] Bid form not found! Order may be closed or already assigned.")
        await browser_manager.close()
        return

    safe_print("[FORM] Bid form found! Testing pricing formula...\n")
    safe_print("="*80)
    safe_print("BID PRICE FORMULA TEST")
    safe_print("="*80)
    safe_print("")
    safe_print(f"{'Bid (RUB)':<12} | {'Author Gets':<15} | {'%':<7} | {'Customer Pays':<15} | {'Multiplier':<10} | {'Platform':<12}")
    safe_print("-" * 90)

    # Тестируем разные ставки
    test_bids = [300, 500, 1000, 1500, 2000, 3000, 5000, 7000, 10000, 15000, 20000]

    results = []

    for bid in test_bids:
        try:
            # Заполняем форму
            await page.fill('#MakeOffer__inputBid', str(bid))
            await page.wait_for_timeout(1200)  # Ждём пересчёта

            # Читаем результат
            price_info = await page.evaluate('''() => {
                const priceDiv = document.querySelector('[class*="PriceInfoDetailedStyled"]');
                if (!priceDiv) return null;

                const bElements = priceDiv.querySelectorAll('b');
                const values = [];

                bElements.forEach(b => {
                    const text = b.textContent.trim();
                    // Извлекаем число (убираем пробелы)
                    const cleaned = text.replace(/[^0-9]/g, '');
                    if (cleaned) {
                        values.push(parseInt(cleaned));
                    }
                });

                return values.length >= 2 ? values : null;
            }''')

            if price_info and len(price_info) >= 2:
                author_gets = price_info[0]
                customer_pays = price_info[1]

                percent = (author_gets / bid * 100) if bid > 0 else 0
                multiplier = (customer_pays / bid) if bid > 0 else 0
                platform_margin = customer_pays - author_gets

                results.append({
                    'bid': bid,
                    'author': author_gets,
                    'percent': percent,
                    'customer': customer_pays,
                    'mult': multiplier,
                    'platform': platform_margin
                })

                safe_print(
                    f"{bid:<12} | {author_gets:<15} | {percent:<6.1f}% | "
                    f"{customer_pays:<15} | x{multiplier:<9.2f} | {platform_margin:<12}"
                )
            else:
                safe_print(f"{bid:<12} | ERROR: Could not parse pricing info")

        except Exception as e:
            safe_print(f"{bid:<12} | ERROR: {e}")

    # Анализ результатов
    safe_print("\n" + "="*80)
    safe_print("ANALYSIS")
    safe_print("="*80 + "\n")

    if results:
        # Средний процент автора
        avg_percent = sum(r['percent'] for r in results) / len(results)
        safe_print(f"Author commission (average): {avg_percent:.2f}%")

        # Диапазон множителя
        min_mult = min(r['mult'] for r in results)
        max_mult = max(r['mult'] for r in results)
        safe_print(f"Customer multiplier range: x{min_mult:.2f} - x{max_mult:.2f}")

        # Зависимость множителя от суммы
        safe_print("\nMultiplier vs Bid amount:")
        for r in results:
            safe_print(f"  {r['bid']:>6} RUB → x{r['mult']:.2f}")

        # Рекомендации для калькулятора
        safe_print("\n" + "="*80)
        safe_print("RECOMMENDATIONS FOR PRICE CALCULATOR")
        safe_print("="*80 + "\n")

        safe_print("1. Author commission is constant: ~97.5%")
        safe_print(f"2. Customer multiplier decreases with bid size: x{max_mult:.2f} -> x{min_mult:.2f}")
        safe_print(f"3. Platform margin: {results[0]['platform']} RUB (small bids) -> {results[-1]['platform']} RUB (large bids)")

        safe_print("\nTo be competitive, we should bid LOWER than competitors:")
        safe_print("  - If budget = 3000 RUB, competitors bid ~2800 RUB")
        safe_print(f"  - Competitor's customer price: 2800 * {[r['mult'] for r in results if r['bid'] == 3000][0] if any(r['bid'] == 3000 for r in results) else 2.1:.2f} = ~6000 RUB")
        safe_print("  - Our bid 2000 RUB:")
        mult_2000 = [r['mult'] for r in results if r['bid'] == 2000][0] if any(r['bid'] == 2000 for r in results) else 2.15
        safe_print(f"  - Our customer price: 2000 * {mult_2000:.2f} = ~{int(2000 * mult_2000)} RUB")
        safe_print(f"  - We're cheaper by ~{int(6000 - 2000 * mult_2000)} RUB!")

        safe_print("\nProposed strategy: bid 60-70% of budget (instead of 85-95%)")

    await browser_manager.close()
    safe_print("\n[DONE] Test complete!")


if __name__ == "__main__":
    asyncio.run(main())
