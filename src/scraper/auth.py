"""Авторизация на Автор24 — логин, cookies, countrylock, проверка сессии."""

import logging
import re
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


async def is_logged_in(page: Page) -> bool:
    """Проверить, залогинен ли пользователь (попробовать открыть /order/search)."""
    try:
        api = browser_manager.context.request
        resp = await api.get(f"{settings.avtor24_base_url}/order/search")
        # Если не редиректит на /login, значит залогинены
        return "/login" not in resp.url
    except Exception as e:
        logger.warning("Ошибка проверки сессии: %s", e)
        return False


async def login() -> Page:
    """Авторизоваться на Автор24. Возвращает страницу."""
    page = await browser_manager.start()
    api = browser_manager.context.request

    # Сначала проверяем сохранённую сессию
    if await is_logged_in(page):
        logger.info("Сессия валидна, логин не требуется")
        return page

    logger.info("Сессия невалидна, выполняем логин...")

    # Шаг 1: GET /login для CSRF-токена
    login_url = f"{settings.avtor24_base_url}/login"
    resp1 = await api.get(login_url)
    body1 = await resp1.text()

    csrf_match = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body1)
    if not csrf_match:
        raise RuntimeError("Не найден CSRF-токен на странице логина")
    csrf_token = csrf_match.group(1)
    logger.debug("CSRF-токен получен: %s...", csrf_token[:10])

    # Шаг 2: POST /login через API (cookies shared с браузером)
    resp2 = await api.post(
        login_url,
        form={
            "ci_csrf_token": csrf_token,
            "email": settings.avtor24_email,
            "password": settings.avtor24_password,
        },
    )
    logger.info("POST /login: status=%d, url=%s", resp2.status, resp2.url)

    # Шаг 3: Обработка countrylock (верификация по телефону)
    if "countrylock" in resp2.url:
        logger.info("Требуется верификация по телефону (countrylock)")
        body2 = await resp2.text()

        if not settings.avtor24_phone_last4:
            raise RuntimeError(
                "Сайт требует верификацию по телефону, но AVTOR24_PHONE_LAST4 не задан в .env"
            )

        # Извлечь CSRF с countrylock-страницы (если есть)
        csrf2_match = re.search(r'name="ci_csrf_token"\s+value="([^"]+)"', body2)
        form_data = {"num": settings.avtor24_phone_last4}
        if csrf2_match:
            form_data["ci_csrf_token"] = csrf2_match.group(1)

        resp3 = await api.post(
            f"{settings.avtor24_base_url}/auth/countrylock/",
            form=form_data,
        )
        logger.info("POST /auth/countrylock/: status=%d, url=%s", resp3.status, resp3.url)

        if "countrylock" in resp3.url:
            body3 = await resp3.text()
            attempts_match = re.search(r"Осталось\s+(\d+)\s+попыт", body3)
            remaining = attempts_match.group(1) if attempts_match else "?"
            raise RuntimeError(
                f"Верификация по телефону не прошла (осталось {remaining} попыток). "
                "Проверьте AVTOR24_PHONE_LAST4 в .env"
            )

    # Шаг 4: Проверяем успешность логина
    resp_check = await api.get(f"{settings.avtor24_base_url}/order/search")
    if "/login" in resp_check.url:
        raise RuntimeError("Не удалось авторизоваться на Автор24 — проверьте учётные данные")

    logger.info("Логин успешен")
    await browser_manager.save_cookies()
    return page


async def refresh_session() -> None:
    """Обновить сессию — зайти на главную для продления cookies."""
    page = browser_manager.page
    if page is None or page.is_closed():
        await login()
        return

    try:
        api = browser_manager.context.request
        await api.get(settings.avtor24_base_url)
        await browser_manager.save_cookies()
        logger.info("Сессия обновлена")
    except Exception as e:
        logger.warning("Ошибка обновления сессии: %s, выполняем повторный логин", e)
        await login()
