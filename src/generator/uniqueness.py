"""Повышение уникальности текста (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "uniqueness_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Раздели текст на 3-5 логических частей для глубокого рерайта каждой части отдельно. "
    "Перефразируй каждую часть, сохраняя смысл и структуру. "
    "Меняй порядок слов, структуру предложений, используй синонимы осмысленно. "
    "Результат должен звучать естественно."
)


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
    """Повысить уникальность текста пошагово: план → разделы → расширение."""
    # Входной текст берём из description или methodology_summary
    source_text = description or methodology_summary or ""
    if not source_text:
        source_text = f"Напиши уникальный текст на тему: \"{title}\""

    sw = await stepwise_generate(
        work_type="Повышение уникальности",
        title=title,
        description=source_text,
        subject=subject,
        pages=pages,
        system_prompt=SYSTEM_PROMPT,
        plan_instructions=PLAN_INSTRUCTIONS,
        required_uniqueness=required_uniqueness,
        temperature=0.8,
    )

    pages_approx = max(1, len(sw.text) // CHARS_PER_PAGE)

    logger.info(
        "Уникальность повышена: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Повышение уникальности текста",
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
