"""
Full parse test: login + countrylock + parse orders + open order detail.
Prints all parsed fields to console.
"""
import asyncio
import json
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

PROXY_SERVER = "http://46.174.194.84:7187"
PROXY_USER = "user358733"
PROXY_PASS = "5ey3ig"
EMAIL = "nikitastarostinn@yandex.ru"
PASSWORD = "63W5T5YCW!H2"
PHONE_LAST4 = "7718"
BASE = "https://avtor24.ru"
OUTPUT_DIR = "tmp/probe"


def safe(text: str) -> str:
    """Safely encode text for console output."""
    if not text:
        return ""
    return text.encode("ascii", "replace").decode("ascii")


async def login_and_get_page(pw, headless=True):
    """Login to Avtor24 and return (browser, context, page)."""
    browser = await pw.chromium.launch(
        headless=headless,
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

    # Step 1: GET /login for CSRF
    resp1 = await api.get(f"{BASE}/login")
    body1 = await resp1.text()
    csrf_match = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body1)
    if not csrf_match:
        raise RuntimeError("No CSRF token found on login page")
    csrf = csrf_match.group(1)

    # Step 2: POST /login
    resp2 = await api.post(f"{BASE}/login", form={
        "ci_csrf_token": csrf,
        "email": EMAIL,
        "password": PASSWORD,
    })

    # Step 3: Handle countrylock
    if "countrylock" in resp2.url:
        body2 = await resp2.text()
        csrf2_m = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body2)
        form_data = {"num": PHONE_LAST4}
        if csrf2_m:
            form_data["ci_csrf_token"] = csrf2_m.group(1)
        resp3 = await api.post(f"{BASE}/auth/countrylock/", form=form_data)
        print(f"  Countrylock: status={resp3.status}, url={resp3.url}")

    # Step 4: Verify login via API
    resp_check = await api.get(f"{BASE}/order/search")
    if "login" in resp_check.url:
        raise RuntimeError(f"Login failed: redirected to {resp_check.url}")

    # Step 5: Open browser page (shared cookies)
    page = await ctx.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx, page


async def parse_orders_page(page):
    """Parse all order cards from the current page."""
    orders = await page.evaluate("""
        () => {
            let cards = document.querySelectorAll('.auctionOrder');
            return Array.from(cards).map(card => {
                let orderId = card.getAttribute('data-id') || '';

                // Title
                let titleEl = card.querySelector('[class*="TitleLinkStyled"] span, [class*="Title-sc"] a span');
                let title = titleEl ? titleEl.textContent.trim() : '';

                // Info fields (.order-info-text spans)
                let infoTexts = Array.from(card.querySelectorAll('.order-info-text')).map(
                    el => el.textContent.trim()
                );

                // Usually: [work_type, deadline, subject, files_info]
                let workType = infoTexts[0] || '';
                let deadline = infoTexts[1] || '';
                let subject = infoTexts[2] || '';
                let filesInfo = infoTexts[3] || '';

                // Description
                let descEl = card.querySelector('[class*="DescriptionStyled"]');
                let description = descEl ? descEl.textContent.trim() : '';

                // Budget
                let budgetEl = card.querySelector('[class*="OrderBudgetStyled"]');
                let budget = budgetEl ? budgetEl.textContent.trim() : '';

                // Bids count (text next to budget, e.g. "4 ставки")
                let offersEl = card.querySelector('[class*="OffersStyled"]');
                let offersText = offersEl ? offersEl.textContent.trim() : '';
                let bidsMatch = offersText.match(/(\\d+)\\s*став/);
                let bidCount = bidsMatch ? parseInt(bidsMatch[1]) : 0;

                // Creation time
                let timeEl = card.querySelector('.orderCreation');
                let creationTime = timeEl ? timeEl.textContent.trim() : '';

                // Customer online status
                let onlineEl = card.querySelector('[class*="CustomerOnlineStyled"]');
                let customerOnline = onlineEl ? onlineEl.textContent.trim() : '';

                // Badges
                let badgeEls = card.querySelectorAll('[class*="Badges"] b, [class*="customer_label"]');
                let badges = Array.from(badgeEls).map(el => el.textContent.trim()).filter(Boolean);

                // URL
                let linkEl = card.querySelector('a[href*="/order/getoneorder/"]');
                let url = linkEl ? linkEl.getAttribute('href') : '';

                return {
                    orderId,
                    title,
                    url,
                    workType,
                    deadline,
                    subject,
                    filesInfo,
                    description,
                    budget,
                    bidCount,
                    creationTime,
                    customerOnline,
                    badges,
                };
            });
        }
    """)
    return orders


async def parse_order_detail(page):
    """Parse order detail from the current page/overlay."""
    detail = await page.evaluate("""
        () => {
            let body = document.body;
            if (!body) return {error: 'no body'};

            // Try to find order detail container
            // Could be a dialog/overlay or a full page
            let container = document.querySelector('[class*="dialog-window-container"]') ||
                            document.querySelector('[class*="Detail"]') ||
                            document.querySelector('[class*="OrderDetail"]') ||
                            document.querySelector('#root');

            if (!container) return {error: 'no container'};

            // Get all text content for overview
            let fullText = container.innerText.substring(0, 8000);

            // Try to extract structured data
            let title = '';
            let titleEl = container.querySelector('[class*="Title"] span, h1, h2, [class*="title"]');
            if (titleEl) title = titleEl.textContent.trim();

            // All info-text fields
            let infoTexts = Array.from(container.querySelectorAll('.order-info-text, [class*="info-text"]')).map(
                el => el.textContent.trim()
            );

            // Description
            let descEl = container.querySelector('[class*="Description"], [class*="description"]');
            let description = descEl ? descEl.textContent.trim() : '';

            // Budget
            let budgetEl = container.querySelector('[class*="Budget"], [class*="budget"], [class*="Price"], [class*="price"]');
            let budget = budgetEl ? budgetEl.textContent.trim() : '';

            return {
                title,
                infoTexts,
                description,
                budget,
                fullText,
            };
        }
    """)
    return detail


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    pw = await async_playwright().start()

    print("=== LOGGING IN ===")
    browser, ctx, page = await login_and_get_page(pw)
    print("  Login successful!")

    # ── Navigate to orders page ──
    print("\n=== LOADING ORDERS PAGE ===")
    await page.goto(f"{BASE}/order/search", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(10)  # Wait for React to render
    print(f"  URL: {page.url}")

    # Close any dialog that might be in the way
    try:
        dialog_close = page.locator('[class*="dialog-window"] [class*="close"], [class*="dialog"] button[class*="close"]')
        if await dialog_close.count() > 0:
            await dialog_close.first.click(timeout=3000)
            await asyncio.sleep(1)
            print("  Closed dialog")
    except Exception:
        pass

    # Count cards
    card_count = await page.locator(".auctionOrder").count()
    print(f"  Order cards found: {card_count}")

    if card_count == 0:
        print("  No cards found, waiting more...")
        await asyncio.sleep(15)
        card_count = await page.locator(".auctionOrder").count()
        print(f"  After wait: {card_count}")

    # ── Parse orders ──
    print("\n=== PARSING ORDERS ===")
    orders = await parse_orders_page(page)

    print(f"\n  Total orders parsed: {len(orders)}\n")
    print("-" * 80)
    for i, order in enumerate(orders[:10]):  # Show first 10
        print(f"  ORDER #{i + 1}")
        print(f"    ID:          {order['orderId']}")
        print(f"    Title:       {safe(order['title'])}")
        print(f"    URL:         {order['url']}")
        print(f"    Work type:   {safe(order['workType'])}")
        print(f"    Subject:     {safe(order['subject'])}")
        print(f"    Deadline:    {safe(order['deadline'])}")
        print(f"    Files:       {safe(order['filesInfo'])}")
        print(f"    Budget:      {safe(order['budget'])}")
        print(f"    Bids:        {order['bidCount']}")
        print(f"    Created:     {safe(order['creationTime'])}")
        print(f"    Customer:    {safe(order['customerOnline'])}")
        print(f"    Badges:      {[safe(b) for b in order['badges']]}")
        print(f"    Description: {safe(order['description'][:200])}")
        print("-" * 80)

    # ── Open first order detail ──
    if orders:
        first_order = orders[0]
        order_url = first_order['url']
        order_id = first_order['orderId']

        print(f"\n=== OPENING ORDER DETAIL: {order_id} ===")
        print(f"  Navigating to {BASE}{order_url} ...")

        # Navigate directly to the detail URL
        await page.goto(f"{BASE}{order_url}", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)
        print(f"  URL: {page.url}")

        # Take screenshot
        await page.screenshot(
            path=f"{OUTPUT_DIR}/order_detail_screenshot.png",
            full_page=False,
            timeout=15000,
        )
        print("  Screenshot saved")

        # Save HTML
        html = await page.content()
        with open(f"{OUTPUT_DIR}/order_detail_rendered.html", "w", encoding="utf-8") as f:
            f.write(html)

        # Parse detail
        detail = await parse_order_detail(page)

        print(f"\n  ORDER DETAIL:")
        print(f"    Title:       {safe(detail.get('title', ''))}")
        print(f"    Info texts:  {[safe(t) for t in detail.get('infoTexts', [])]}")
        print(f"    Description: {safe(detail.get('description', '')[:500])}")
        print(f"    Budget:      {safe(detail.get('budget', ''))}")

        full_text = detail.get('fullText', '')
        safe_full = safe(full_text[:4000])
        print(f"\n  Full page text:\n{safe_full}")

        # Also try to find all relevant classes on the detail page
        detail_classes = await page.evaluate("""
            () => {
                let allClasses = new Set();
                document.querySelectorAll('[class]').forEach(el => {
                    let cn = el.getAttribute('class');
                    if (cn) cn.split(/\\s+/).forEach(c => {
                        if (c.includes('order') || c.includes('Order') || c.includes('detail') ||
                            c.includes('Detail') || c.includes('bid') || c.includes('Bid') ||
                            c.includes('info') || c.includes('Info') || c.includes('price') ||
                            c.includes('Price') || c.includes('budget') || c.includes('Budget') ||
                            c.includes('unique') || c.includes('Unique') || c.includes('file') ||
                            c.includes('File') || c.includes('description') || c.includes('Description') ||
                            c.includes('customer') || c.includes('Customer') || c.includes('subject') ||
                            c.includes('Subject') || c.includes('deadline') || c.includes('Deadline'))
                            allClasses.add(c);
                    });
                });
                return Array.from(allClasses).sort();
            }
        """)
        print(f"\n  Detail page CSS classes:")
        for cls in detail_classes:
            print(f"    .{cls}")

    # Save all orders as JSON
    with open(f"{OUTPUT_DIR}/parsed_orders.json", "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    print(f"\n  All orders saved to {OUTPUT_DIR}/parsed_orders.json")

    await browser.close()
    await pw.stop()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
