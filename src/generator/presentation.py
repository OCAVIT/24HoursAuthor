"""Генератор текста для презентаций."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "presentation_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class GenerationResult:
    """Результат генерации презентации."""
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
    pages: int = 15,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать текст для презентации (слайды + заметки докладчика)."""
    # pages здесь = количество слайдов
    slides_count = max(10, pages)

    user_prompt = _build_prompt(
        title=title,
        description=description,
        subject=subject,
        slides_count=slides_count,
        methodology_summary=methodology_summary,
    )

    result = await chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.openai_model_main,
        temperature=0.7,
        max_tokens=8000,
    )

    text = result["content"]
    pages_approx = max(1, len(text) // CHARS_PER_PAGE)

    logger.info(
        "Презентация сгенерирована: '%s', ~%d слайдов, %d токенов, $%.4f",
        title[:50], slides_count, result["total_tokens"], result["cost_usd"],
    )

    return GenerationResult(
        text=text,
        title=title,
        work_type="Презентации",
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
    slides_count: int,
    methodology_summary: Optional[str],
) -> str:
    """Построить промпт для генерации презентации."""
    parts = [
        f"Создай текст для презентации на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Количество слайдов: {slides_count}",
    ]

    if description:
        parts.append(f"\nДополнительные требования:\n{description}")

    if methodology_summary:
        parts.append(f"\nИнформация из методички:\n{methodology_summary}")

    parts.append(
        f"\nСоздай {slides_count} слайдов в формате:\n"
        "СЛАЙД N: [Заголовок]\n"
        "- Тезис 1\n"
        "- Тезис 2\n"
        "- Тезис 3\n"
        "ЗАМЕТКИ ДОКЛАДЧИКА: [Подробный текст для выступления]\n\n"
        "Первый слайд — титульный, последний — 'Спасибо за внимание'."
    )

    return "\n".join(p for p in parts if p)
