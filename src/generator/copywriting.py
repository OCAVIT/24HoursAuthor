"""Генератор текстов: копирайтинг, рерайт, набор текста."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "copywriting_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class GenerationResult:
    """Результат генерации текста."""
    text: str
    title: str
    work_type: str
    pages_approx: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 5,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать текст (копирайтинг / рерайт / набор текста)."""
    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(2000, target_chars // 3))

    user_prompt = _build_prompt(
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        target_chars=target_chars,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
    )

    result = await chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.openai_model_main,
        temperature=0.7,
        max_tokens=max_tokens,
    )

    text = result["content"]
    pages_approx = max(1, len(text) // CHARS_PER_PAGE)

    logger.info(
        "Копирайтинг сгенерирован: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, result["total_tokens"], result["cost_usd"],
    )

    return GenerationResult(
        text=text,
        title=title,
        work_type="Копирайтинг",
        pages_approx=pages_approx,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


def _build_prompt(
    title: str,
    description: str,
    subject: str,
    pages: int,
    target_chars: int,
    methodology_summary: Optional[str],
    required_uniqueness: Optional[int],
) -> str:
    """Построить промпт для копирайтинга."""
    parts = [
        f"Задание: \"{title}\"",
        f"Тематика: {subject}" if subject else "",
        f"Требуемый объём: {pages} страниц ({target_chars} символов с пробелами)",
    ]

    if description:
        parts.append(f"\nОписание задания:\n{description}")

    if methodology_summary:
        parts.append(f"\nДополнительная информация:\n{methodology_summary}")

    if required_uniqueness:
        parts.append(f"\nТребуемая уникальность: не менее {required_uniqueness}%.")

    parts.append("\nНапиши текст согласно заданию. Сохраняй требуемый стиль и объём.")

    return "\n".join(p for p in parts if p)
