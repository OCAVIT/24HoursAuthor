"""Проверка уникальности через API ETXT."""

import asyncio
import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

ETXT_API_URL = "https://www.etxt.ru/api/"
POLL_INTERVAL = 10  # секунд между проверками статуса
MAX_POLL_ATTEMPTS = 60  # максимум попыток (10 минут)


async def check(text: str) -> float:
    """Проверить уникальность текста через ETXT API.

    Args:
        text: Текст для проверки.

    Returns:
        Процент уникальности (0.0-100.0).

    Raises:
        RuntimeError: Если API недоступен или ключ невалиден.
    """
    if not settings.etxt_api_key:
        raise RuntimeError("ETXT_API_KEY не задан")

    task_id = await _submit_text(text)
    if not task_id:
        raise RuntimeError("Не удалось отправить текст на проверку ETXT")

    uniqueness = await _poll_result(task_id)
    return uniqueness


async def _submit_text(text: str) -> Optional[str]:
    """Отправить текст на проверку и получить ID задачи."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                ETXT_API_URL,
                data={
                    "method": "text_check",
                    "token": settings.etxt_api_key,
                    "text": text,
                },
            )
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                logger.error("ETXT API ошибка: %s", data.get("error"))
                raise RuntimeError(f"ETXT error: {data.get('error')}")

            task_id = str(data.get("id", ""))
            if task_id:
                logger.info("ETXT: текст отправлен, task_id=%s", task_id)
            return task_id or None

        except httpx.HTTPError as e:
            logger.error("Ошибка HTTP при отправке в ETXT: %s", e)
            raise RuntimeError(f"ETXT HTTP error: {e}") from e


async def _poll_result(task_id: str) -> float:
    """Опрашивать API ETXT до получения результата."""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL)

            try:
                response = await client.post(
                    ETXT_API_URL,
                    data={
                        "method": "text_check_result",
                        "token": settings.etxt_api_key,
                        "id": task_id,
                    },
                )
                response.raise_for_status()
                data = response.json()

                # Ещё обрабатывается
                status = data.get("status")
                if status == "processing":
                    logger.debug("ETXT: проверка в процессе, попытка %d/%d", attempt + 1, MAX_POLL_ATTEMPTS)
                    continue

                if data.get("error"):
                    logger.error("ETXT poll ошибка: %s", data.get("error"))
                    raise RuntimeError(f"ETXT poll error: {data.get('error')}")

                # Результат готов
                uniqueness = data.get("unique")
                if uniqueness is not None:
                    result = float(uniqueness)
                    logger.info("ETXT: уникальность = %.1f%%", result)
                    return result

            except httpx.HTTPError as e:
                logger.warning("ETXT poll HTTP ошибка (попытка %d): %s", attempt + 1, e)
                continue

    raise RuntimeError(f"ETXT: таймаут проверки после {MAX_POLL_ATTEMPTS} попыток")
