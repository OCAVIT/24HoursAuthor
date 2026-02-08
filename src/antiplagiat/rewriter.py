"""Рерайтер для повышения уникальности текста через GPT-4o."""

import logging
from dataclasses import dataclass
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings
from src.docgen.formatter import split_into_paragraphs

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = """Ты — профессиональный редактор академических текстов.
Твоя задача — перефразировать текст, сохраняя:
- Полный смысл и все факты
- Академический стиль изложения
- Научную терминологию
- Структуру и логику аргументации

Правила перефразирования:
- Меняй структуру предложений (инверсия, замена активных/пассивных конструкций)
- Используй синонимы, но НЕ примитивный синонимайзер
- Пиши как живой автор, а не как робот
- Сохраняй длину текста (±10%)
- НЕ добавляй новую информацию
- НЕ удаляй существующую информацию
- НЕ добавляй маркеры или комментарии
- Верни ТОЛЬКО перефразированный текст, без пояснений"""

MAX_REWRITE_ITERATIONS = 3
# Максимум символов за один вызов API (~8 страниц)
MAX_CHARS_PER_CALL = 14000


@dataclass
class RewriteResult:
    """Результат рерайта."""
    text: str
    iterations: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def rewrite_for_uniqueness(
    text: str,
    target_percent: float,
    current_percent: Optional[float] = None,
    max_iterations: int = MAX_REWRITE_ITERATIONS,
) -> RewriteResult:
    """Перефразировать текст для повышения уникальности.

    Если текст длинный — разбивает на части и рерайтит каждую.

    Args:
        text: Исходный текст.
        target_percent: Целевая уникальность (%).
        current_percent: Текущая уникальность (для информации в промпте).
        max_iterations: Максимум итераций рерайта.

    Returns:
        RewriteResult с перефразированным текстом и метаданными.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0

    # Разбиваем на части если текст слишком большой
    if len(text) > MAX_CHARS_PER_CALL:
        rewritten_text, tokens_info = await _rewrite_in_chunks(text, target_percent, current_percent)
        total_input += tokens_info["input_tokens"]
        total_output += tokens_info["output_tokens"]
        total_cost += tokens_info["cost_usd"]
    else:
        rewritten_text, tokens_info = await _rewrite_chunk(text, target_percent, current_percent)
        total_input += tokens_info["input_tokens"]
        total_output += tokens_info["output_tokens"]
        total_cost += tokens_info["cost_usd"]

    logger.info(
        "Рерайт завершён: %d → %d символов, %d input + %d output токенов, $%.4f",
        len(text), len(rewritten_text), total_input, total_output, total_cost,
    )

    return RewriteResult(
        text=rewritten_text,
        iterations=1,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_input + total_output,
        cost_usd=total_cost,
    )


async def _rewrite_chunk(
    text: str,
    target_percent: float,
    current_percent: Optional[float] = None,
) -> tuple[str, dict]:
    """Перефразировать один фрагмент текста."""
    user_prompt = _build_rewrite_prompt(text, target_percent, current_percent)

    # Рерайт требует примерно столько же токенов на выходе, сколько на входе
    max_tokens = min(16000, max(2000, len(text) // 3))

    result = await chat_completion(
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.openai_model_main,
        temperature=0.8,
        max_tokens=max_tokens,
    )

    tokens_info = {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }

    return result["content"], tokens_info


async def _rewrite_in_chunks(
    text: str,
    target_percent: float,
    current_percent: Optional[float] = None,
) -> tuple[str, dict]:
    """Разбить текст на части и перефразировать каждую."""
    paragraphs = split_into_paragraphs(text)
    chunks = _group_paragraphs_into_chunks(paragraphs, MAX_CHARS_PER_CALL)

    total_input = 0
    total_output = 0
    total_cost = 0.0
    rewritten_parts = []

    for i, chunk in enumerate(chunks):
        logger.debug("Рерайт чанка %d/%d (%d символов)", i + 1, len(chunks), len(chunk))
        rewritten, tokens_info = await _rewrite_chunk(chunk, target_percent, current_percent)
        rewritten_parts.append(rewritten)
        total_input += tokens_info["input_tokens"]
        total_output += tokens_info["output_tokens"]
        total_cost += tokens_info["cost_usd"]

    tokens_info = {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": total_cost,
    }

    return "\n\n".join(rewritten_parts), tokens_info


def _group_paragraphs_into_chunks(paragraphs: list[str], max_chars: int) -> list[str]:
    """Сгруппировать абзацы в чанки не длиннее max_chars."""
    chunks = []
    current_chunk: list[str] = []
    current_length = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 для \n\n
        if current_length + para_len > max_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_length = 0
        current_chunk.append(para)
        current_length += para_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks if chunks else ["\n\n".join(paragraphs)]


def _build_rewrite_prompt(
    text: str,
    target_percent: float,
    current_percent: Optional[float] = None,
) -> str:
    """Построить промпт для рерайта."""
    parts = ["Перефразируй следующий академический текст."]

    if current_percent is not None:
        parts.append(
            f"Текущая уникальность: {current_percent:.0f}%. "
            f"Нужно достичь: {target_percent:.0f}%."
        )
    else:
        parts.append(f"Целевая уникальность: {target_percent:.0f}%.")

    parts.append(
        "Перефразируй как можно сильнее, меняя структуру предложений, "
        "но сохраняя смысл и академический стиль."
    )

    parts.append(f"\nТекст для перефразирования:\n\n{text}")

    return "\n".join(parts)
