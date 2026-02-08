"""Повышение уникальности текста (рерайт входного текста)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "uniqueness_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class GenerationResult:
    """Результат повышения уникальности."""
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
    pages: int = 10,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Повысить уникальность текста через глубокий рерайт."""
    # Входной текст берём из description или methodology_summary
    source_text = description or methodology_summary or ""

    if not source_text:
        # Если нет текста для рерайта — генерируем новый по теме
        source_text = f"Напиши уникальный текст на тему: \"{title}\""

    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(2000, target_chars // 3))

    user_prompt = _build_prompt(
        title=title,
        source_text=source_text,
        required_uniqueness=required_uniqueness,
    )

    result = await chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.openai_model_main,
        temperature=0.8,
        max_tokens=max_tokens,
    )

    text = result["content"]
    pages_approx = max(1, len(text) // CHARS_PER_PAGE)

    logger.info(
        "Уникальность повышена: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, result["total_tokens"], result["cost_usd"],
    )

    return GenerationResult(
        text=text,
        title=title,
        work_type="Повышение уникальности текста",
        pages_approx=pages_approx,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


def _build_prompt(
    title: str,
    source_text: str,
    required_uniqueness: Optional[int],
) -> str:
    """Построить промпт для повышения уникальности."""
    target = required_uniqueness or 70

    parts = [
        f"Задание: повысить уникальность текста до {target}% и выше.",
        f"Тема работы: \"{title}\"",
        f"\nИсходный текст для рерайта:\n{source_text}",
        "\nПерефразируй весь текст, сохраняя смысл и структуру. "
        "Меняй порядок слов, структуру предложений, используй синонимы осмысленно. "
        "Результат должен звучать естественно.",
    ]

    return "\n".join(parts)
