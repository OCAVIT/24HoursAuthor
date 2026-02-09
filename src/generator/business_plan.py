"""Генератор бизнес-планов (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "business_plan_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Используй стандартные разделы бизнес-плана: "
    "1. Резюме проекта, 2. Описание продукта/услуги, 3. Анализ рынка, "
    "4. Маркетинговая стратегия, 5. Организационный план, "
    "6. Производственный план, 7. Финансовый план, 8. Анализ рисков. "
    "Каждый раздел — отдельный блок с расчётами и обоснованиями."
)


@dataclass
class GenerationResult:
    """Результат генерации бизнес-плана."""
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
    """Сгенерировать бизнес-план пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Бизнес-план",
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        system_prompt=SYSTEM_PROMPT,
        plan_instructions=PLAN_INSTRUCTIONS,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
        temperature=0.6,
    )

    pages_approx = max(1, len(sw.text) // CHARS_PER_PAGE)

    logger.info(
        "Бизнес-план сгенерирован: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Бизнес-план",
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
