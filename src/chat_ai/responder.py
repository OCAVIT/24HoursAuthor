"""AI-респондер для ведения диалога с заказчиком."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "chat_system.txt").read_text(encoding="utf-8")

# Слова, которые не должны появляться в ответе
BANNED_WORDS = [
    "ai", "нейросеть", "нейросети", "нейросетью", "нейросетей",
    "chatgpt", "gpt", "искусственный интеллект", "искусственного интеллекта",
    "ии", "openai", "бот", "автоматически сгенерирован",
    "языковая модель", "языковой модели",
]


@dataclass
class ChatResponse:
    """Результат генерации ответа в чат."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate_response(
    order_description: str,
    message_history: list[dict],
    new_message: str,
    order_status: str = "",
    work_type: str = "",
    subject: str = "",
    deadline: str = "",
    required_uniqueness: Optional[int] = None,
    antiplagiat_system: str = "",
) -> ChatResponse:
    """Сгенерировать ответ заказчику.

    Args:
        order_description: Описание заказа (тема, требования).
        message_history: История переписки [{role, content}, ...].
        new_message: Новое сообщение от заказчика.
        order_status: Текущий статус работы.
        work_type: Тип работы (Эссе, Курсовая и т.д.).
        subject: Предмет.
        deadline: Дедлайн.
        required_uniqueness: Требуемая уникальность (%).
        antiplagiat_system: Система антиплагиата.
    """
    context = _build_context(
        order_description=order_description,
        order_status=order_status,
        work_type=work_type,
        subject=subject,
        deadline=deadline,
        required_uniqueness=required_uniqueness,
        antiplagiat_system=antiplagiat_system,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст заказа:\n{context}"},
    ]

    for msg in message_history:
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    messages.append({"role": "user", "content": new_message})

    result = await chat_completion(
        messages=messages,
        model=settings.openai_model_fast,
        temperature=0.7,
        max_tokens=256,
    )

    text = result["content"].strip()
    text = _sanitize_response(text)

    logger.info(
        "Чат-ответ сгенерирован: %d токенов, $%.4f",
        result["total_tokens"], result["cost_usd"],
    )

    return ChatResponse(
        text=text,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


def _build_context(
    order_description: str,
    order_status: str,
    work_type: str,
    subject: str,
    deadline: str,
    required_uniqueness: Optional[int],
    antiplagiat_system: str,
) -> str:
    """Построить контекст заказа для промпта."""
    parts = []

    if work_type:
        parts.append(f"Тип работы: {work_type}")
    if subject:
        parts.append(f"Предмет: {subject}")
    if deadline:
        parts.append(f"Дедлайн: {deadline}")
    if order_status:
        parts.append(f"Статус: {order_status}")
    if required_uniqueness:
        parts.append(f"Требуемая уникальность: {required_uniqueness}%")
    if antiplagiat_system:
        parts.append(f"Система антиплагиата: {antiplagiat_system}")
    if order_description:
        parts.append(f"\nОписание заказа:\n{order_description}")

    return "\n".join(parts)


def _sanitize_response(text: str) -> str:
    """Проверить ответ на запрещённые слова и убрать их."""
    lower = text.lower()
    for word in BANNED_WORDS:
        if word in lower:
            logger.warning("Обнаружено запрещённое слово в ответе: '%s'", word)
    return text
