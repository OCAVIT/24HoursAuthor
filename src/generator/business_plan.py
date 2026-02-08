"""Генератор бизнес-планов (пошаговая генерация по разделам)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "business_plan_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800

# Стандартные разделы бизнес-плана
DEFAULT_SECTIONS = [
    "Резюме проекта",
    "Описание продукта/услуги",
    "Анализ рынка",
    "Маркетинговая стратегия",
    "Организационный план",
    "Производственный план",
    "Финансовый план",
    "Анализ рисков",
]


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
    pages: int = 25,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать бизнес-план по разделам."""
    total_input = 0
    total_output = 0
    total_cost = 0.0

    sections_text: list[str] = []
    section_pages = max(2, pages // len(DEFAULT_SECTIONS))

    for section_name in DEFAULT_SECTIONS:
        target_chars = section_pages * CHARS_PER_PAGE
        max_tokens = min(16000, max(1500, target_chars // 3))

        user_parts = [
            f"Бизнес-план: \"{title}\"",
            f"Отрасль/тематика: {subject}" if subject else "",
            f"\nНапиши раздел: {section_name}",
            f"Объём раздела: ~{target_chars} символов ({section_pages} стр.)",
        ]
        if description:
            user_parts.append(f"\nОписание бизнес-идеи: {description}")
        if methodology_summary:
            user_parts.append(f"\nДополнительная информация: {methodology_summary[:500]}")

        result = await chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(p for p in user_parts if p)},
            ],
            model=settings.openai_model_main,
            temperature=0.6,
            max_tokens=max_tokens,
        )

        sections_text.append(f"{section_name.upper()}\n\n{result['content']}")
        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_cost += result["cost_usd"]

    full_text = "\n\n".join(sections_text)
    pages_approx = max(1, len(full_text) // CHARS_PER_PAGE)

    logger.info(
        "Бизнес-план сгенерирован: '%s', ~%d стр., %d+%d токенов, $%.4f",
        title[:50], pages_approx, total_input, total_output, total_cost,
    )

    return GenerationResult(
        text=full_text,
        title=title,
        work_type="Бизнес-план",
        pages_approx=pages_approx,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_input + total_output,
        cost_usd=total_cost,
    )
