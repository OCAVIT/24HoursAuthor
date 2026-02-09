"""Extract DOM selectors for order cards from the rendered orders page."""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright
import re

PROXY_SERVER = "http://46.174.194.84:7187"
PROXY_USER = "user358733"
PROXY_PASS = "5ey3ig"
EMAIL = "nikitastarostinn@yandex.ru"
PASSWORD = "63W5T5YCW!H2"
PHONE_LAST4 = "7718"
BASE = "https://avtor24.ru"
OUTPUT_DIR = "tmp/probe"


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        proxy={"server": PROXY_SERVER, "username": PROXY_USER, "password": PROXY_PASS},
    )
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )
    api = ctx.request

    # Login
    print("Logging in...")
    resp1 = await api.get(f"{BASE}/login")
    body1 = await resp1.text()
    csrf = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body1).group(1)

    resp2 = await api.post(f"{BASE}/login", form={
        "ci_csrf_token": csrf, "email": EMAIL, "password": PASSWORD,
    })

    if "countrylock" in resp2.url:
        body2 = await resp2.text()
        csrf2_m = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body2)
        form_data = {"num": PHONE_LAST4}
        if csrf2_m:
            form_data["ci_csrf_token"] = csrf2_m.group(1)
        await api.post(f"{BASE}/auth/countrylock/", form=form_data)

    print("Logged in. Opening orders page in browser...")
    page = await ctx.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    await page.goto(f"{BASE}/order/search", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(10)  # Wait for React to fully render

    print(f"URL: {page.url}")

    # Check how many .auctionOrder cards exist
    count = await page.locator(".auctionOrder").count()
    print(f"\n.auctionOrder cards: {count}")

    if count == 0:
        # Maybe we need to wait more or scroll
        print("Waiting more...")
        await asyncio.sleep(10)
        count = await page.locator(".auctionOrder").count()
        print(f".auctionOrder cards after wait: {count}")

    if count > 0:
        # Extract simplified card info
        result = await page.evaluate("""
            () => {
                let cards = document.querySelectorAll('.auctionOrder');
                let firstCard = cards[0];
                if (!firstCard) return {error: 'no card'};

                // Get data attributes
                let attrs = {};
                for (let attr of firstCard.attributes) {
                    attrs[attr.name] = attr.value.substring(0, 200);
                }

                // Find all <a> links inside the card
                let links = Array.from(firstCard.querySelectorAll('a')).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: a.innerText.substring(0, 100),
                    className: String(a.className || '').substring(0, 200),
                }));

                // Find all clickable elements
                let clickable = Array.from(firstCard.querySelectorAll('[onclick], button, a')).map(el => ({
                    tag: el.tagName,
                    className: String(el.className || '').substring(0, 200),
                    text: el.innerText.substring(0, 100),
                    href: el.getAttribute('href') || '',
                }));

                // Find ALL classes used in the card
                let allClasses = new Set();
                firstCard.querySelectorAll('*').forEach(el => {
                    let cn = el.getAttribute('class');
                    if (cn) cn.split(/\s+/).forEach(c => allClasses.add(c));
                });

                return {
                    cardCount: cards.length,
                    firstCardAttrs: attrs,
                    links,
                    clickable,
                    allClasses: Array.from(allClasses).sort(),
                    htmlLength: firstCard.outerHTML.length,
                };
            }
        """)

        print(f"\nCard count: {result['cardCount']}")
        print(f"\nFirst card attributes: {json.dumps(result['firstCardAttrs'], ensure_ascii=False, indent=2)}")
        print(f"\nLinks in card:")
        for link in result['links']:
            text = link['text'].encode('ascii', 'replace').decode('ascii')
            print(f"  href={link['href']} class={link['className'][:80]} text={text}")
        print(f"\nClickable elements:")
        for el in result['clickable']:
            text = el['text'].encode('ascii', 'replace').decode('ascii')[:80]
            print(f"  <{el['tag']}> class={el['className'][:80]} text={text} href={el['href']}")
        print(f"\nAll CSS classes in card:")
        for cls in result['allClasses']:
            print(f"  .{cls}")

        # Save the first card HTML for detailed analysis
        first_card_html = await page.evaluate("""
            () => {
                let card = document.querySelector('.auctionOrder');
                return card ? card.outerHTML : '';
            }
        """)
        with open(f"{OUTPUT_DIR}/first_card.html", "w", encoding="utf-8") as f:
            f.write(first_card_html)
        print(f"\nFirst card HTML saved ({len(first_card_html)} chars)")

        # Now try clicking the order title to navigate to detail page
        print("\n=== CLICKING ORDER TITLE ===")
        # Find the title link in the first card
        title_link = await page.evaluate("""
            () => {
                let card = document.querySelector('.auctionOrder');
                if (!card) return null;
                // Look for the title element - typically largest text or specific class
                let allLinks = card.querySelectorAll('a');
                for (let link of allLinks) {
                    let href = link.getAttribute('href') || '';
                    // Skip if it's a user profile link or other non-order link
                    if (href.includes('/order/') || href.includes('/auction/')) {
                        return {href, text: link.innerText.substring(0, 200)};
                    }
                }
                // If no order link, return the first non-empty link
                for (let link of allLinks) {
                    if (link.innerText.trim().length > 10) {
                        return {href: link.getAttribute('href'), text: link.innerText.substring(0, 200)};
                    }
                }
                return null;
            }
        """)
        if title_link:
            text = title_link['text'].encode('ascii', 'replace').decode('ascii')
            print(f"  Title link: href={title_link['href']}, text={text}")

        # Click on the order title in the first card
        first_card = page.locator(".auctionOrder").first
        # Try to find a clickable title element inside
        title_el = first_card.locator("a").first
        if await title_el.count() > 0:
            print("  Clicking first <a> in card...")
            await title_el.click()
            await asyncio.sleep(5)
            new_url = page.url
            print(f"  URL after click: {new_url}")

            if new_url != f"{BASE}/order/search":
                # We navigated to order detail!
                await page.screenshot(
                    path=f"{OUTPUT_DIR}/order_detail_screenshot.png",
                    full_page=False,
                    timeout=15000,
                )
                detail_html = await page.content()
                with open(f"{OUTPUT_DIR}/order_detail_rendered.html", "w", encoding="utf-8") as f:
                    f.write(detail_html)
                print("  Detail page saved!")

                # Extract detail data
                detail_text = await page.evaluate(
                    "() => document.body ? document.body.innerText.substring(0, 8000) : ''"
                )
                safe_text = detail_text[:4000].encode('ascii', 'replace').decode('ascii')
                print(f"\n  Detail text:\n{safe_text}")
            else:
                print("  Still on orders page. Trying to click the order card div itself...")
                # Maybe clicking the card opens a modal
                await first_card.click()
                await asyncio.sleep(5)
                new_url2 = page.url
                print(f"  URL after card click: {new_url2}")
    else:
        print("No order cards found!")

    await browser.close()
    await pw.stop()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
