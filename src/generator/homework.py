"""Генератор контрольных работ, решения задач, ответов на вопросы и лабораторных
(пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "homework_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Раздели задание на логические блоки: для каждого вопроса/задачи — отдельный раздел. "
    "Если задание содержит несколько вопросов, каждый вопрос — отдельный раздел. "
    "Если задание одно — раздели на: постановка задачи, решение/анализ, ответ/выводы. "
    "Для задач: Условие → Дано → Решение → Ответ. "
    "Для вопросов: развёрнутый ответ с определениями и примерами."
)


@dataclass
class GenerationResult:
    """Результат генерации контрольной/задач."""
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
    pages: int = 8,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать контрольную/задачи пошагово: план → разделы → расширение."""
    sw = await stepwise_generate(
        work_type="Контрольная работа",
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        system_prompt=SYSTEM_PROMPT,
        plan_instructions=PLAN_INSTRUCTIONS,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
        temperature=0.5,
    )

    pages_approx = max(1, len(sw.text) // CHARS_PER_PAGE)

    logger.info(
        "Контрольная сгенерирована: '%s', ~%d стр., %d токенов, $%.4f",
        title[:50], pages_approx, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Контрольная работа",
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
