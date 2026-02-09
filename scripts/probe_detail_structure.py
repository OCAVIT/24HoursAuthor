"""Probe: extract detail page DOM structure for selector mapping."""
import asyncio
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.async_api import async_playwright

PROXY_SERVER = "http://46.174.194.84:7187"
PROXY_USER = "user358733"
PROXY_PASS = "5ey3ig"
EMAIL = "nikitastarostinn@yandex.ru"
PASSWORD = "63W5T5YCW!H2"
PHONE_LAST4 = "7718"
BASE = "https://avtor24.ru"


def safe(text):
    if not text:
        return ""
    return text.encode("ascii", "replace").decode("ascii")


async def main():
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
        csrf2 = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body2)
        form = {"num": PHONE_LAST4}
        if csrf2:
            form["ci_csrf_token"] = csrf2.group(1)
        await api.post(f"{BASE}/auth/countrylock/", form=form)
    print("Login OK")

    page = await ctx.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    # Get first order ID from the search page
    print("Loading orders page...")
    await page.goto(f"{BASE}/order/search", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(10)

    first_id = await page.evaluate("""
        () => {
            let card = document.querySelector('.auctionOrder');
            return card ? card.getAttribute('data-id') : null;
        }
    """)
    print(f"First order ID: {first_id}")

    if not first_id:
        print("No orders found!")
        await browser.close()
        await pw.stop()
        return

    # Navigate to detail page
    detail_url = f"{BASE}/order/getoneorder/{first_id}"
    print(f"Opening detail: {detail_url}")
    await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)

    # Extract DOM structure
    structure = await page.evaluate("""
        () => {
            let root = document.querySelector('#root');
            if (!root) return {error: 'no #root'};

            // 1. Find all elements with relevant class patterns
            let classMap = {};
            root.querySelectorAll('[class]').forEach(el => {
                let cls = el.getAttribute('class') || '';
                // Only styled-components and meaningful classes
                let matches = cls.match(/styled__\\w+/g) || [];
                matches.forEach(m => {
                    if (!classMap[m]) {
                        let text = el.textContent.trim().substring(0, 200);
                        let tag = el.tagName.toLowerCase();
                        let childCount = el.children.length;
                        classMap[m] = {class: cls.substring(0, 150), tag, childCount, text};
                    }
                });
            });

            // 2. Find all order-info-text elements and their contexts
            let infoTexts = [];
            root.querySelectorAll('.order-info-text').forEach(el => {
                let parent = el.parentElement;
                let parentClass = parent ? (parent.getAttribute('class') || '').substring(0, 100) : '';
                infoTexts.push({
                    text: el.textContent.trim().substring(0, 100),
                    parentTag: parent ? parent.tagName : '',
                    parentClass,
                });
            });

            // 3. Find label-value pairs by looking at the page structure
            // Look for elements that contain field labels
            let labelValuePairs = [];
            let allText = root.innerText;
            let labels = [
                'Тип работы', 'Предмет', 'Срок сдачи', 'Номер заказа',
                'Минимальный объём', 'Кол-во страниц', 'Шрифт', 'Интервал',
                'Оригинальность', 'Антиплагиат', 'Бюджет заказчика',
                'Описание заказа', 'Средняя ставка', 'Гарантия'
            ];
            for (let label of labels) {
                let idx = allText.indexOf(label);
                if (idx >= 0) {
                    // Get next 200 chars after label
                    let after = allText.substring(idx, idx + 300);
                    let lines = after.split('\\n').map(l => l.trim()).filter(Boolean);
                    labelValuePairs.push({label, nextLines: lines.slice(0, 5)});
                }
            }

            // 4. Find file download links
            let fileLinks = [];
            root.querySelectorAll('a[href]').forEach(a => {
                let href = a.getAttribute('href') || '';
                if (href.includes('/file') || href.includes('/download') || href.includes('attachment')) {
                    fileLinks.push({href, text: a.textContent.trim().substring(0, 100)});
                }
            });

            // 5. Get the detailed section containers
            let containers = [];
            root.querySelectorAll('[class*="styled__"]').forEach(el => {
                let cls = el.getAttribute('class') || '';
                if (el.children.length > 0 && el.children.length < 20) {
                    let directText = '';
                    for (let child of el.childNodes) {
                        if (child.nodeType === 3) directText += child.textContent.trim() + ' ';
                    }
                    if (directText.trim()) {
                        containers.push({
                            class: cls.substring(0, 120),
                            directText: directText.trim().substring(0, 200),
                            childCount: el.children.length,
                        });
                    }
                }
            });

            // 6. Full page text (first 5000 chars)
            let pageText = root.innerText.substring(0, 5000);

            return {
                classMap,
                infoTexts,
                labelValuePairs,
                fileLinks,
                containers,
                pageText,
            };
        }
    """)

    print("\n=== STYLED CLASS MAP ===")
    for key, val in structure.get('classMap', {}).items():
        print(f"  {key}")
        print(f"    tag={val['tag']}, children={val['childCount']}")
        print(f"    text: {safe(val['text'][:120])}")
        print()

    print("\n=== ORDER-INFO-TEXT ELEMENTS ===")
    for info in structure.get('infoTexts', []):
        print(f"  text: {safe(info['text'])}")
        print(f"    parent: {info['parentTag']} class={safe(info['parentClass'][:80])}")

    print("\n=== LABEL-VALUE PAIRS ===")
    for pair in structure.get('labelValuePairs', []):
        print(f"  {safe(pair['label'])}:")
        for line in pair['nextLines']:
            print(f"    -> {safe(line[:100])}")

    print("\n=== FILE LINKS ===")
    for link in structure.get('fileLinks', []):
        print(f"  {safe(link['text'][:60])} -> {link['href'][:100]}")

    print("\n=== FULL PAGE TEXT (first 3000 chars) ===")
    print(safe(structure.get('pageText', '')[:3000]))

    await browser.close()
    await pw.stop()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
