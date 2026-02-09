"""Генератор рефератов, докладов и статей (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "referat_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Включи: введение (актуальность, цель, задачи, обзор структуры), "
    "3-4 главы по теме, заключение (выводы по каждой главе, итог), "
    "список литературы (10-15 источников по ГОСТ Р 7.0.5-2008). "
    "Подразделы внутри глав указывай как отдельные разделы (например '1.1 Подраздел')."
)


@dataclass
class ReferatPlan:
    """Оглавление реферата (для обратной совместимости)."""
    title: str
    introduction: bool = True
    chapters: list[dict] = field(default_factory=list)
    conclusion: bool = True
    bibliography: bool = True


@dataclass
class GenerationResult:
    """Результат генерации реферата."""
    text: str
    title: str
    work_type: str
    plan: Optional[ReferatPlan] = None
    pages_approx: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 12,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать реферат пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Реферат",
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

    plan = ReferatPlan(title=title)
    for s in sw.plan:
        name_lower = s.name.lower()
        if "введен" not in name_lower and "заключен" not in name_lower and "литератур" not in name_lower:
            plan.chapters.append({"title": s.name, "target_words": s.target_words})

    logger.info(
        "Реферат сгенерирован: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Реферат",
        plan=plan,
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )


def _plan_to_text(plan: ReferatPlan) -> str:
    """Преобразовать план в текстовый формат (для обратной совместимости тестов)."""
    lines = ["Введение"]
    for ch in plan.chapters:
        lines.append(ch.get("title", ""))
    lines.append("Заключение")
    lines.append("Список литературы")
    return "\n".join(lines)
