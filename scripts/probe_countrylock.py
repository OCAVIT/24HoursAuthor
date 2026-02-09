"""
Probe: save and analyze the countrylock page content (143KB).
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

    # Step 1: GET /login for CSRF
    print("1) GET /login ...")
    resp1 = await api.get(f"{BASE}/login")
    body1 = await resp1.text()
    csrf_match = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body1)
    csrf_token = csrf_match.group(1) if csrf_match else None
    print(f"   CSRF: {csrf_token}")

    # Step 2: POST /login
    print("2) POST /login ...")
    resp2 = await api.post(
        f"{BASE}/login",
        form={
            "ci_csrf_token": csrf_token,
            "email": EMAIL,
            "password": PASSWORD,
        },
    )
    body2 = await resp2.text()
    print(f"   Status: {resp2.status}, URL: {resp2.url}")
    print(f"   Body length: {len(body2)}")

    # Save the full countrylock page
    with open(f"{OUTPUT_DIR}/countrylock_full.html", "w", encoding="utf-8") as f:
        f.write(body2)
    print(f"   Saved to {OUTPUT_DIR}/countrylock_full.html")

    # Analyze the page content
    print("\n=== ANALYSIS ===")

    # Look for forms
    forms = re.findall(r'<form[^>]*>(.*?)</form>', body2, re.DOTALL | re.IGNORECASE)
    print(f"   Forms found: {len(forms)}")
    for i, form in enumerate(forms):
        print(f"\n   Form {i}:")
        action_match = re.search(r'action="([^"]*)"', form)
        method_match = re.search(r'method="([^"]*)"', form)
        if action_match:
            print(f"     action: {action_match.group(1)}")
        if method_match:
            print(f"     method: {method_match.group(1)}")
        inputs = re.findall(r'<input[^>]*>', form)
        for inp in inputs:
            print(f"     input: {inp[:200]}")
        buttons = re.findall(r'<button[^>]*>.*?</button>', form, re.DOTALL)
        for btn in buttons:
            print(f"     button: {btn[:200]}")

    # Look for scripts with relevant content
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', body2, re.DOTALL | re.IGNORECASE)
    print(f"\n   Scripts found: {len(scripts)}")
    for i, script in enumerate(scripts):
        if len(script.strip()) > 10:  # Skip empty scripts
            # Check for interesting patterns
            if any(kw in script.lower() for kw in ['countrylock', 'country', 'redirect', 'location', 'submit', 'ajax', 'fetch', 'xmlhttp']):
                print(f"\n   Script {i} (relevant, {len(script)} chars):")
                print(f"     {script[:1000]}")

    # Look for links/buttons related to country selection
    country_elements = re.findall(r'country[^"]*', body2, re.IGNORECASE)
    print(f"\n   Elements with 'country' in attributes: {len(country_elements)}")
    for elem in country_elements[:10]:
        print(f"     {elem[:200]}")

    # Look for body content
    body_match = re.search(r'<body[^>]*>(.*?)</body>', body2, re.DOTALL | re.IGNORECASE)
    if body_match:
        body_content = body_match.group(1)
        # Strip scripts and styles for readable text
        text = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        print(f"\n   Body text ({len(text)} chars):")
        print(f"     {text[:2000]}")
    else:
        print("\n   NO <body> tag found!")
        # Maybe the body is outside of standard tags
        # Print around the middle of the document
        mid = len(body2) // 2
        print(f"   Middle of document (chars {mid-500} to {mid+500}):")
        print(f"     {body2[max(0,mid-500):mid+500]}")

    # Check for meta refresh or JS redirect
    meta_refresh = re.findall(r'<meta[^>]*http-equiv="refresh"[^>]*>', body2, re.IGNORECASE)
    if meta_refresh:
        print(f"\n   Meta refresh: {meta_refresh}")

    js_redirect = re.findall(r'(window\.location|document\.location|location\.href)[^;]*;', body2)
    if js_redirect:
        print(f"\n   JS redirects: {js_redirect}")

    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
