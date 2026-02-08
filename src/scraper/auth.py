"""Авторизация на Автор24 — логин, cookies, проверка сессии."""

import logging
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


async def is_logged_in(page: Page) -> bool:
    """Проверить, залогинен ли пользователь (наличие ссылки на профиль/кабинет)."""
    try:
        await page.goto(settings.avtor24_base_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.short_delay()

        # Ищем признаки авторизации: ссылка на кабинет, аватар, кнопка "Выход"
        logged = await page.locator(
            'a[href*="/cabinet"], a[href*="/user/profile"], .user-menu, .header-user'
        ).count()
        return logged > 0
    except Exception as e:
        logger.warning("Ошибка проверки сессии: %s", e)
        return False


async def login() -> Page:
    """Авторизоваться на Автор24. Возвращает страницу."""
    page = await browser_manager.start()

    # Сначала проверяем сохранённую сессию
    if await is_logged_in(page):
        logger.info("Сессия валидна, логин не требуется")
        return page

    logger.info("Сессия невалидна, выполняем логин...")

    # Переходим на страницу логина
    login_url = f"{settings.avtor24_base_url}/user/login"
    await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
    await browser_manager.short_delay()

    # Заполняем форму
    email_input = page.locator('input[name="email"], input[type="email"], #email')
    password_input = page.locator('input[name="password"], input[type="password"], #password')

    await email_input.fill(settings.avtor24_email)
    await browser_manager.short_delay()
    await password_input.fill(settings.avtor24_password)
    await browser_manager.short_delay()

    # Нажимаем кнопку "Войти"
    submit_btn = page.locator(
        'button[type="submit"], input[type="submit"], .login-btn, button:has-text("Войти")'
    )
    await submit_btn.click()

    # Ждём навигацию (редирект после логина)
    await page.wait_for_load_state("domcontentloaded", timeout=15000)
    await browser_manager.short_delay()

    # Проверяем успешность
    if await is_logged_in(page):
        logger.info("Логин успешен")
        await browser_manager.save_cookies()
    else:
        logger.error("Логин не удался — проверьте учётные данные")
        raise RuntimeError("Не удалось авторизоваться на Автор24")

    return page


async def refresh_session() -> None:
    """Обновить сессию — зайти на главную для продления cookies."""
    page = browser_manager.page
    if page is None or page.is_closed():
        await login()
        return

    try:
        await page.goto(settings.avtor24_base_url, wait_until="domcontentloaded", timeout=30000)
        await browser_manager.save_cookies()
        logger.info("Сессия обновлена")
    except Exception as e:
        logger.warning("Ошибка обновления сессии: %s, выполняем повторный логин", e)
        await login()
