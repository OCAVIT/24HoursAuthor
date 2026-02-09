"""Генератор эссе и сочинений (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "essay_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Раздели эссе на 3-5 логических разделов: "
    "введение (актуальность, постановка проблемы), "
    "2-3 основных тезиса с аргументацией и примерами, "
    "заключение (выводы, личная позиция). "
    "Не добавляй список литературы."
)


@dataclass
class GenerationResult:
    """Результат генерации работы."""
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
    """Сгенерировать эссе/сочинение пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Эссе",
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        system_prompt=SYSTEM_PROMPT,
        plan_instructions=PLAN_INSTRUCTIONS,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
        temperature=0.7,
    )

    pages_approx = max(1, len(sw.text) // CHARS_PER_PAGE)

    logger.info(
        "Эссе сгенерировано: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Эссе",
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
