"""Генератор курсовых работ (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "coursework_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Включи: введение (актуальность, степень изученности, цель, задачи, объект, предмет, методы, структура), "
    "2-3 главы по 2-3 подраздела в каждой, заключение (выводы по каждой главе, степень достижения цели, "
    "практическая значимость, рекомендации), список литературы (15-25 источников по ГОСТ). "
    "Подразделы указывай как отдельные разделы (например 'Глава 1. ...', '1.1 ...', '1.2 ...')."
)


@dataclass
class CourseworkPlan:
    """Оглавление курсовой работы (для обратной совместимости)."""
    title: str
    introduction: bool = True
    chapters: list[dict] = field(default_factory=list)
    conclusion: bool = True
    bibliography: bool = True


@dataclass
class GenerationResult:
    """Результат генерации курсовой работы."""
    text: str
    title: str
    work_type: str
    plan: Optional[CourseworkPlan] = None
    pages_approx: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 25,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать курсовую пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Курсовая работа",
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

    plan = CourseworkPlan(title=title)
    for s in sw.plan:
        name_lower = s.name.lower()
        if "глава" in name_lower or (s.name[0:1].isdigit() and "." in s.name[:4]):
            plan.chapters.append({"title": s.name, "target_words": s.target_words})

    logger.info(
        "Курсовая сгенерирована: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Курсовая работа",
        plan=plan,
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
