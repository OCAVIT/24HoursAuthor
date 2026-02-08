"""Антибан — обнаружение блокировки, пауза, лимиты, защита от бана."""

import asyncio
import logging
import time
from datetime import date, datetime
from typing import Optional

from playwright.async_api import Page, Response

from src.config import settings

logger = logging.getLogger(__name__)

# Максимум ставок в день (естественный лимит автора)
MAX_DAILY_BIDS = 20

# Пауза при обнаружении бана (секунды)
BAN_PAUSE_SECONDS = 30 * 60  # 30 минут

# Состояние антибан-модуля
_ban_detected_at: Optional[float] = None
_ban_reason: str = ""


def is_banned() -> bool:
    """Проверить, находимся ли мы в режиме паузы после бана."""
    if _ban_detected_at is None:
        return False
    elapsed = time.time() - _ban_detected_at
    if elapsed >= BAN_PAUSE_SECONDS:
        # Пауза истекла, сбрасываем
        clear_ban()
        logger.info("Пауза после бана истекла, возобновляем работу")
        return False
    remaining = int(BAN_PAUSE_SECONDS - elapsed)
    logger.warning("Бан активен, осталось %d сек паузы", remaining)
    return True


def ban_remaining_seconds() -> int:
    """Сколько секунд осталось до конца паузы."""
    if _ban_detected_at is None:
        return 0
    elapsed = time.time() - _ban_detected_at
    remaining = max(0, int(BAN_PAUSE_SECONDS - elapsed))
    return remaining


def set_ban(reason: str = "") -> None:
    """Установить режим бана (пауза на 30 мин)."""
    global _ban_detected_at, _ban_reason
    _ban_detected_at = time.time()
    _ban_reason = reason
    logger.error("БАН ОБНАРУЖЕН: %s. Пауза %d мин.", reason, BAN_PAUSE_SECONDS // 60)


def clear_ban() -> None:
    """Сбросить состояние бана."""
    global _ban_detected_at, _ban_reason
    _ban_detected_at = None
    _ban_reason = ""


def get_ban_info() -> dict:
    """Информация о текущем бане для дашборда."""
    return {
        "is_banned": is_banned(),
        "reason": _ban_reason,
        "remaining_seconds": ban_remaining_seconds(),
        "detected_at": datetime.fromtimestamp(_ban_detected_at).isoformat() if _ban_detected_at else None,
    }


async def check_response_for_ban(response: Optional[Response]) -> bool:
    """Проверить ответ сервера на признаки бана.

    Returns:
        True если обнаружен бан (403, captcha и т.д.).
    """
    if response is None:
        return False

    # HTTP 403 Forbidden
    if response.status == 403:
        set_ban("HTTP 403 Forbidden")
        return True

    # HTTP 429 Too Many Requests
    if response.status == 429:
        set_ban("HTTP 429 Too Many Requests")
        return True

    return False


async def check_page_for_ban(page: Page) -> bool:
    """Проверить текущую страницу на признаки бана/captcha.

    Returns:
        True если обнаружен бан.
    """
    try:
        # Проверяем наличие captcha
        captcha_selectors = [
            "iframe[src*='captcha']",
            "iframe[src*='recaptcha']",
            ".g-recaptcha",
            "#captcha",
            ".captcha",
            "div[class*='captcha']",
            "img[alt*='captcha']",
        ]
        for selector in captcha_selectors:
            if await page.locator(selector).count() > 0:
                set_ban(f"Обнаружена captcha: {selector}")
                return True

        # Проверяем текст страницы на признаки блокировки
        body_text = await page.locator("body").inner_text()
        body_lower = body_text.lower()
        ban_phrases = [
            "доступ ограничен",
            "доступ заблокирован",
            "вы были заблокированы",
            "слишком много запросов",
            "access denied",
            "forbidden",
            "blocked",
            "temporarily unavailable",
        ]
        for phrase in ban_phrases:
            if phrase in body_lower:
                set_ban(f"Обнаружена фраза блокировки: '{phrase}'")
                return True

    except Exception as e:
        logger.debug("Ошибка проверки страницы на бан: %s", e)

    return False


async def check_daily_bid_limit(bids_today: int) -> bool:
    """Проверить, не превышен ли дневной лимит ставок.

    Returns:
        True если лимит НЕ превышен и можно ставить.
    """
    if bids_today >= MAX_DAILY_BIDS:
        logger.warning(
            "Дневной лимит ставок достигнут: %d/%d. Ставки приостановлены до завтра.",
            bids_today, MAX_DAILY_BIDS,
        )
        return False
    return True


async def safe_goto(page: Page, url: str, **kwargs) -> Optional[Response]:
    """Безопасный переход на страницу с проверкой бана.

    Если обнаружен бан — устанавливает паузу и возвращает None.
    """
    if is_banned():
        return None

    try:
        response = await page.goto(url, **kwargs)

        # Проверяем HTTP-ответ
        if await check_response_for_ban(response):
            return None

        # Проверяем содержимое страницы
        if await check_page_for_ban(page):
            return None

        return response

    except Exception as e:
        logger.error("Ошибка при переходе на %s: %s", url, e)
        raise
