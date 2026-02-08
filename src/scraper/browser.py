"""Playwright browser manager — singleton, прокси, UA ротация, случайные задержки, антибан."""

import asyncio
import random
import json
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Locator

from src.config import settings

logger = logging.getLogger(__name__)

# Реальные Chrome User-Agent строки
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Популярные разрешения экрана
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
]

COOKIES_PATH = Path("cookies.json")


class BrowserManager:
    """Singleton менеджер Playwright-браузера."""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._user_agent: str = random.choice(USER_AGENTS)
        self._viewport: dict = random.choice(VIEWPORTS)

    async def start(self) -> Page:
        """Запуск браузера и создание страницы."""
        if self._page and not self._page.is_closed():
            return self._page

        self._playwright = await async_playwright().start()

        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }

        # Прокси
        if settings.proxy_ru:
            launch_args["proxy"] = {"server": settings.proxy_ru}

        self._browser = await self._playwright.chromium.launch(**launch_args)

        context_args = {
            "user_agent": self._user_agent,
            "viewport": self._viewport,
            "locale": "ru-RU",
            "timezone_id": "Europe/Moscow",
        }

        # Загрузка сохранённых cookies
        if COOKIES_PATH.exists():
            try:
                cookies = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
                self._context = await self._browser.new_context(**context_args)
                await self._context.add_cookies(cookies)
                logger.info("Cookies загружены из %s", COOKIES_PATH)
            except Exception as e:
                logger.warning("Не удалось загрузить cookies: %s", e)
                self._context = await self._browser.new_context(**context_args)
        else:
            self._context = await self._browser.new_context(**context_args)

        self._page = await self._context.new_page()

        # Скрытие webdriver-флага
        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        logger.info("Браузер запущен: UA=%s, viewport=%s", self._user_agent, self._viewport)
        return self._page

    async def save_cookies(self) -> None:
        """Сохранить cookies в файл."""
        if self._context is None:
            return
        cookies = await self._context.cookies()
        COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        logger.info("Cookies сохранены в %s", COOKIES_PATH)

    async def random_delay(self, min_sec: Optional[float] = None, max_sec: Optional[float] = None) -> None:
        """Случайная задержка для имитации человека."""
        lo = min_sec if min_sec is not None else settings.speed_limit_min_delay
        hi = max_sec if max_sec is not None else settings.speed_limit_max_delay
        delay = random.uniform(lo, hi)
        logger.debug("Задержка %.1f сек", delay)
        await asyncio.sleep(delay)

    async def short_delay(self) -> None:
        """Короткая задержка (1-3 сек) для переходов между страницами."""
        await asyncio.sleep(random.uniform(1.0, 3.0))

    async def human_click(self, locator: Locator) -> None:
        """Имитация человеческого клика: движение мыши к элементу + задержка перед кликом."""
        if self._page is None:
            return
        try:
            box = await locator.bounding_box()
            if box:
                # Случайная точка внутри элемента (не центр — так естественнее)
                x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
                y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
                # Плавное движение мыши
                await self._page.mouse.move(x, y, steps=random.randint(5, 15))
                await asyncio.sleep(random.uniform(0.1, 0.4))
                await self._page.mouse.click(x, y)
            else:
                await locator.click()
        except Exception:
            # Фоллбэк — обычный клик
            await locator.click()

    async def human_type(self, locator: Locator, text: str) -> None:
        """Имитация человеческого набора текста — посимвольный ввод со случайными задержками."""
        await locator.focus()
        await asyncio.sleep(random.uniform(0.2, 0.5))
        for char in text:
            await locator.press_sequentially(char, delay=random.randint(30, 120))
            # Иногда делать микропаузу (как человек думает)
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.3, 0.8))

    @property
    def page(self) -> Optional[Page]:
        """Текущая страница."""
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        """Текущий контекст браузера."""
        return self._context

    async def close(self) -> None:
        """Закрыть браузер и Playwright."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._page = None
        logger.info("Браузер закрыт")


# Singleton экземпляр
browser_manager = BrowserManager()
