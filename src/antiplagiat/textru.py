"""Проверка уникальности через API text.ru."""

import asyncio
import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

TEXTRU_API_URL = "https://api.text.ru/post"
POLL_INTERVAL = 10  # секунд между проверками статуса
MAX_POLL_ATTEMPTS = 60  # максимум попыток (10 минут)


async def check(text: str) -> float:
    """Проверить уникальность текста через text.ru API.

    Args:
        text: Текст для проверки.

    Returns:
        Процент уникальности (0.0-100.0).

    Raises:
        RuntimeError: Если API недоступен или ключ невалиден.
    """
    if not settings.textru_api_key:
        raise RuntimeError("TEXTRU_API_KEY не задан")

    uid = await _submit_text(text)
    if not uid:
        raise RuntimeError("Не удалось отправить текст на проверку text.ru")

    uniqueness = await _poll_result(uid)
    return uniqueness


async def _submit_text(text: str) -> Optional[str]:
    """Отправить текст на проверку и получить uid задачи."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                TEXTRU_API_URL,
                json={
                    "text": text,
                    "userkey": settings.textru_api_key,
                },
            )
            response.raise_for_status()
            data = response.json()

            if "error_code" in data:
                logger.error("text.ru API ошибка: %s — %s", data.get("error_code"), data.get("error_desc"))
                raise RuntimeError(f"text.ru error: {data.get('error_desc', 'unknown')}")

            uid = data.get("text_uid")
            if uid:
                logger.info("text.ru: текст отправлен, uid=%s", uid)
            return uid

        except httpx.HTTPError as e:
            logger.error("Ошибка HTTP при отправке в text.ru: %s", e)
            raise RuntimeError(f"text.ru HTTP error: {e}") from e


async def _poll_result(uid: str) -> float:
    """Опрашивать API text.ru до получения результата."""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL)

            try:
                response = await client.post(
                    TEXTRU_API_URL,
                    json={
                        "uid": uid,
                        "userkey": settings.textru_api_key,
                        "jsonvisible": "detail",
                    },
                )
                response.raise_for_status()
                data = response.json()

                # Текст ещё проверяется
                if "error_code" in data and data["error_code"] == 181:
                    logger.debug("text.ru: проверка в процессе, попытка %d/%d", attempt + 1, MAX_POLL_ATTEMPTS)
                    continue

                if "error_code" in data:
                    logger.error("text.ru poll ошибка: %s", data.get("error_desc"))
                    raise RuntimeError(f"text.ru poll error: {data.get('error_desc')}")

                # Результат готов
                uniqueness = data.get("unique")
                if uniqueness is not None:
                    result = float(uniqueness)
                    logger.info("text.ru: уникальность = %.1f%%", result)
                    return result

            except httpx.HTTPError as e:
                logger.warning("text.ru poll HTTP ошибка (попытка %d): %s", attempt + 1, e)
                continue

    raise RuntimeError(f"text.ru: таймаут проверки после {MAX_POLL_ATTEMPTS} попыток")
