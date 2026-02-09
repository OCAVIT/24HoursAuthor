"""Генератор текста для презентаций (пошаговая генерация: план → разделы → расширение)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.generator.stepwise import stepwise_generate, CHARS_PER_PAGE

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "presentation_system.txt").read_text(encoding="utf-8")

PLAN_INSTRUCTIONS = (
    "Раздели презентацию на группы слайдов: "
    "титульный слайд, 3-5 тематических групп по 2-4 слайда в каждой, заключительный слайд. "
    "Каждая группа — отдельный раздел. "
    "Формат каждого раздела:\n"
    "СЛАЙД N: [Заголовок]\n- Тезис 1\n- Тезис 2\n- Тезис 3\n"
    "ЗАМЕТКИ ДОКЛАДЧИКА: [Подробный текст для выступления]\n"
    "Первый слайд — титульный, последний — 'Спасибо за внимание'."
)


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
    """Сгенерировать текст презентации пошагово: план → группы слайдов → расширение."""
    slides_count = max(10, pages)

    sw = await stepwise_generate(
        work_type="Презентация",
        title=title,
        description=description,
        subject=subject,
        pages=slides_count,
        system_prompt=SYSTEM_PROMPT,
        plan_instructions=PLAN_INSTRUCTIONS,
        methodology_summary=methodology_summary,
        temperature=0.7,
    )

    pages_approx = max(1, len(sw.text) // CHARS_PER_PAGE)

    logger.info(
        "Презентация сгенерирована: '%s', ~%d слайдов, %d токенов, $%.4f",
        title[:50], slides_count, sw.total_tokens, sw.cost_usd,
    )

    return GenerationResult(
        text=sw.text,
        title=title,
        work_type="Презентации",
        pages_approx=pages_approx,
        input_tokens=sw.input_tokens,
        output_tokens=sw.output_tokens,
        total_tokens=sw.total_tokens,
        cost_usd=sw.cost_usd,
    )
