"""Probe: dump full structure of order cards to find customer name elements."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper.browser import browser_manager
from src.scraper.auth import login


async def main():
    page = await login()

    await page.goto("https://avtor24.ru/order/search", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector(".auctionOrder", timeout=15000)
    await asyncio.sleep(3)

    # Dump structure of first 3 cards
    data = await page.evaluate("""
        () => {
            let cards = document.querySelectorAll('.auctionOrder');
            let results = [];
            let count = Math.min(cards.length, 3);
            for (let i = 0; i < count; i++) {
                let card = cards[i];

                // All elements with class containing "customer" or "Customer"
                let custEls = card.querySelectorAll('[class*="customer"], [class*="Customer"]');
                let custData = Array.from(custEls).map(el => ({
                    tag: el.tagName,
                    className: el.className.substring(0, 120),
                    text: el.innerText.substring(0, 200),
                    html: el.innerHTML.substring(0, 500)
                }));

                // All <a> tags
                let links = Array.from(card.querySelectorAll('a')).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: a.textContent.trim().substring(0, 100),
                    className: a.className.substring(0, 80)
                }));

                // All elements with "user" in href or class
                let userEls = card.querySelectorAll('[href*="user"], [class*="user"], [class*="User"]');
                let userData = Array.from(userEls).map(el => ({
                    tag: el.tagName,
                    className: el.className.substring(0, 120),
                    href: el.getAttribute('href') || '',
                    text: el.textContent.trim().substring(0, 200)
                }));

                // Full text of the card
                let fullText = card.innerText.substring(0, 1000);

                // HTML snapshot (first 2000 chars)
                let htmlSnapshot = card.innerHTML.substring(0, 2000);

                results.push({
                    orderId: card.getAttribute('data-id'),
                    custData,
                    links,
                    userData,
                    fullText,
                    htmlSnapshot
                });
            }
            return results;
        }
    """)

    os.makedirs("tmp/probe", exist_ok=True)
    with open("tmp/probe/card_structure.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Print summary
    for i, card in enumerate(data):
        print(f"\n=== Card {i+1} (ID: {card['orderId']}) ===")
        print(f"Customer elements: {len(card['custData'])}")
        for c in card['custData']:
            t = c['text'].encode('ascii', 'replace').decode('ascii')
            print(f"  [{c['tag']}] class={c['className'][:60]} text={t[:100]}")
        print(f"User elements: {len(card['userData'])}")
        for u in card['userData']:
            t = u['text'].encode('ascii', 'replace').decode('ascii')
            print(f"  [{u['tag']}] href={u['href'][:60]} text={t[:100]}")
        print(f"Links: {len(card['links'])}")
        for lnk in card['links']:
            t = lnk['text'].encode('ascii', 'replace').decode('ascii')
            print(f"  href={lnk['href'][:60]} text={t[:60]}")

    await browser_manager.close()
    print("\nFull data saved to tmp/probe/card_structure.json")


if __name__ == "__main__":
    asyncio.run(main())
