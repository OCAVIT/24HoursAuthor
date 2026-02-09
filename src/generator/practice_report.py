"""Генератор отчётов по практике (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "practice_report_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Включи: введение (цель практики, задачи, место прохождения, сроки), "
    "характеристика организации, описание выполненных работ, анализ полученного опыта, "
    "заключение (выводы, полученный опыт, предложения), "
    "список литературы (5-10 источников по ГОСТ)."
)


@dataclass
class ReportPlan:
    """Оглавление отчёта по практике (для обратной совместимости)."""
    title: str
    chapters: list[dict] = field(default_factory=list)


@dataclass
class GenerationResult:
    """Результат генерации отчёта по практике."""
    text: str
    title: str
    work_type: str
    plan: Optional[ReportPlan] = None
    pages_approx: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 20,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать отчёт по практике пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Отчёт по практике",
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

    plan = ReportPlan(title=title)
    for s in sw.plan:
        name_lower = s.name.lower()
        if "введен" not in name_lower and "заключен" not in name_lower and "литератур" not in name_lower:
            plan.chapters.append({"title": s.name, "target_words": s.target_words})

    logger.info(
        "Отчёт по практике сгенерирован: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Отчёт по практике",
        plan=plan,
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
