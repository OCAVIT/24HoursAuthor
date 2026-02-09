"""
Probe avtor24.ru — login + countrylock verification + parse orders.
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

    # ── Step 1: GET /login for CSRF ──
    print("=== 1. GET /login ===")
    resp1 = await api.get(f"{BASE}/login")
    body1 = await resp1.text()
    csrf_match = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body1)
    csrf_token = csrf_match.group(1) if csrf_match else None
    print(f"  CSRF: {csrf_token}")

    if not csrf_token:
        print("FAILED - no CSRF token")
        await browser.close()
        await pw.stop()
        return

    # ── Step 2: POST /login ──
    print("\n=== 2. POST /login ===")
    resp2 = await api.post(
        f"{BASE}/login",
        form={
            "ci_csrf_token": csrf_token,
            "email": EMAIL,
            "password": PASSWORD,
        },
    )
    body2 = await resp2.text()
    print(f"  Status: {resp2.status}, URL: {resp2.url}")
    print(f"  Body length: {len(body2)}")

    # ── Step 3: Handle countrylock verification ──
    if "countrylock" in resp2.url:
        print("\n=== 3. COUNTRYLOCK VERIFICATION ===")
        print(f"  Sending phone last 4 digits: {PHONE_LAST4}")

        # Extract new CSRF token from countrylock page
        csrf_match2 = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body2)
        csrf_token2 = csrf_match2.group(1) if csrf_match2 else None
        print(f"  Countrylock CSRF: {csrf_token2}")

        # Submit the countrylock form
        form_data = {"num": PHONE_LAST4}
        if csrf_token2:
            form_data["ci_csrf_token"] = csrf_token2

        resp3 = await api.post(
            f"{BASE}/auth/countrylock/",
            form=form_data,
        )
        body3 = await resp3.text()
        print(f"  Status: {resp3.status}, URL: {resp3.url}")
        print(f"  Body length: {len(body3)}")

        # Check if we got past countrylock
        if "countrylock" in resp3.url:
            print("  Still on countrylock! Checking for error message...")
            error_match = re.search(r'<div[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</div>', body3, re.DOTALL)
            if error_match:
                print(f"  Error: {error_match.group(1).strip()}")
            # Try saving the page
            with open(f"{OUTPUT_DIR}/countrylock_after_submit.html", "w", encoding="utf-8") as f:
                f.write(body3)
            print(f"  Saved countrylock page after submit")

            # Check remaining attempts
            attempts_match = re.search(r'Осталось\s+(\d+)\s+попыт', body3)
            if attempts_match:
                print(f"  Remaining attempts: {attempts_match.group(1)}")
        else:
            print(f"  Countrylock passed! Redirected to: {resp3.url}")
    else:
        print("  No countrylock! Directly logged in.")

    # ── Step 4: Check if logged in ──
    print("\n=== 4. CHECK LOGIN STATUS ===")
    resp4 = await api.get(f"{BASE}/")
    body4 = await resp4.text()
    print(f"  Status: {resp4.status}, URL: {resp4.url}")

    logged_indicators = {
        "cabinet": "cabinet" in body4.lower(),
        "profile": "profile" in body4.lower(),
        "выход": "выход" in body4.lower(),
        "logout": "logout" in body4.lower(),
        "Мои заказы": "мои заказы" in body4.lower(),
    }
    print(f"  Login indicators: {logged_indicators}")
    is_logged = any(logged_indicators.values())
    print(f"  LOGGED IN: {is_logged}")

    if not is_logged:
        with open(f"{OUTPUT_DIR}/homepage_after_login.html", "w", encoding="utf-8") as f:
            f.write(body4)
        print("  Saved homepage for analysis")

        # Also check /order/search
        print("\n  Trying /order/search via API...")
        resp4b = await api.get(f"{BASE}/order/search")
        print(f"  Status: {resp4b.status}, URL: {resp4b.url}")

        if "/login" not in resp4b.url:
            is_logged = True
            print("  Actually logged in! /order/search accessible")
        else:
            print("  Not logged in - /order/search redirects to login")
            await browser.close()
            await pw.stop()
            return

    # ── Step 5: Parse orders page ──
    print("\n=== 5. ORDERS PAGE ===")
    resp5 = await api.get(f"{BASE}/order/search")
    body5 = await resp5.text()
    print(f"  Status: {resp5.status}, URL: {resp5.url}")
    print(f"  Body length: {len(body5)}")

    with open(f"{OUTPUT_DIR}/orders_page.html", "w", encoding="utf-8") as f:
        f.write(body5)
    print(f"  Saved orders page HTML")

    # ── Step 6: Now open in browser (shared cookies) ──
    print("\n=== 6. BROWSER: ORDERS PAGE ===")
    page = await ctx.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    await page.goto(f"{BASE}/order/search", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(5)

    browser_url = page.url
    print(f"  Browser URL: {browser_url}")

    if "/login" not in browser_url and "/countrylock" not in browser_url:
        # Take screenshot
        await page.screenshot(path=f"{OUTPUT_DIR}/orders_screenshot.png", full_page=False, timeout=15000)
        print("  Screenshot saved")

        # Get page text
        body_text = await page.evaluate(
            "() => document.body ? document.body.innerText.substring(0, 5000) : 'NO BODY'"
        )
        safe_text = body_text[:3000].encode("ascii", "replace").decode("ascii")
        print(f"\n  Page text:\n{safe_text}")

        # Save full HTML
        html = await page.content()
        with open(f"{OUTPUT_DIR}/orders_browser.html", "w", encoding="utf-8") as f:
            f.write(html)

        # ── Step 7: Try clicking on first order ──
        print("\n=== 7. CLICK FIRST ORDER ===")

        # Try different selectors for order cards
        selectors = [
            ".order-card",
            ".search-result-item",
            ".order-item",
            "[data-order-id]",
            ".cx-order-card",
            "a[href*='/order/']",
            ".order-list__item",
            ".order-list-item",
            ".work-item",
            ".search-item",
        ]

        order_link = None
        for sel in selectors:
            count = await page.locator(sel).count()
            if count > 0:
                print(f"  Found {count} elements with selector: {sel}")
                # Get first element's text and href
                first = page.locator(sel).first
                text = await first.inner_text(timeout=5000)
                safe_el_text = text[:200].encode("ascii", "replace").decode("ascii")
                print(f"  First element text: {safe_el_text}")

                # Try to get href
                href = await first.get_attribute("href")
                if href:
                    print(f"  href: {href}")
                    order_link = href

                # Click on the first order
                print(f"  Clicking first element...")
                await first.click()
                await asyncio.sleep(5)
                print(f"  URL after click: {page.url}")

                if page.url != browser_url:
                    # We navigated — get details
                    detail_text = await page.evaluate(
                        "() => document.body ? document.body.innerText.substring(0, 5000) : 'NO BODY'"
                    )
                    safe_detail = detail_text[:3000].encode("ascii", "replace").decode("ascii")
                    print(f"\n  Order detail text:\n{safe_detail}")

                    detail_html = await page.content()
                    with open(f"{OUTPUT_DIR}/order_detail.html", "w", encoding="utf-8") as f:
                        f.write(detail_html)
                    print("  Saved order detail HTML")

                    await page.screenshot(
                        path=f"{OUTPUT_DIR}/order_detail_screenshot.png",
                        full_page=False,
                        timeout=15000,
                    )
                    print("  Detail screenshot saved")
                break
        else:
            print("  No order elements found with any selector!")
            # Let's inspect the actual DOM structure
            print("\n  Inspecting DOM structure...")
            structure = await page.evaluate("""
                () => {
                    let body = document.body;
                    if (!body) return 'NO BODY';
                    // Get all elements with class names
                    let elements = body.querySelectorAll('[class]');
                    let classes = new Set();
                    for (let el of elements) {
                        for (let c of el.classList) {
                            if (c.includes('order') || c.includes('search') || c.includes('card') || c.includes('item') || c.includes('list')) {
                                classes.add(c + ' -> ' + el.tagName);
                            }
                        }
                    }
                    return Array.from(classes).join('\\n');
                }
            """)
            print(f"  Relevant classes:\n{structure}")

            # Also check for links to /order/
            links = await page.evaluate("""
                () => {
                    let links = document.querySelectorAll('a[href*="/order/"]');
                    return Array.from(links).slice(0, 10).map(a => ({
                        href: a.href,
                        text: a.innerText.substring(0, 100),
                        class: a.className
                    }));
                }
            """)
            print(f"\n  Links to /order/:")
            for link in links:
                print(f"    {link}")

    else:
        print(f"  FAILED - browser redirected to {browser_url}")
        html = await page.content()
        with open(f"{OUTPUT_DIR}/browser_failed.html", "w", encoding="utf-8") as f:
            f.write(html)

    # Save cookies
    cookies = await ctx.cookies()
    with open(f"{OUTPUT_DIR}/final_cookies.json", "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"\n  Cookies saved ({len(cookies)})")

    await browser.close()
    await pw.stop()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
