"""Генератор отчётов по практике (учебной, производственной, преддипломной)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "practice_report_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class ReportPlan:
    """Оглавление отчёта по практике."""
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
    """Сгенерировать отчёт по практике пошагово."""
    total_input = 0
    total_output = 0
    total_cost = 0.0

    # Шаг 1: Генерация плана
    plan, tokens_info = await _generate_plan(title, description, subject, pages, methodology_summary)
    total_input += tokens_info["input_tokens"]
    total_output += tokens_info["output_tokens"]
    total_cost += tokens_info["cost_usd"]

    sections: list[str] = []
    plan_text = _plan_to_text(plan)

    # Введение
    intro_pages = max(1, pages // 10)
    intro, t = await _generate_section(
        title, subject, plan_text,
        "Напиши введение отчёта по практике: цель, задачи, место прохождения, сроки.",
        pages=intro_pages, methodology_summary=methodology_summary,
    )
    sections.append("ВВЕДЕНИЕ\n\n" + intro)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Основные разделы
    available_pages = pages - intro_pages - 2 - 1  # минус введение, заключение, литература
    chapter_pages = max(3, available_pages // max(1, len(plan.chapters)))

    for chapter in plan.chapters:
        ch_title = chapter.get("title", "")
        text, t = await _generate_section(
            title, subject, plan_text,
            f"Напиши раздел '{ch_title}' отчёта по практике.\nОбъём: {chapter_pages} страниц.",
            pages=chapter_pages, methodology_summary=methodology_summary,
        )
        sections.append(f"{ch_title.upper()}\n\n{text}")
        total_input += t["input_tokens"]
        total_output += t["output_tokens"]
        total_cost += t["cost_usd"]

    # Заключение
    conclusion, t = await _generate_section(
        title, subject, plan_text,
        "Напиши заключение отчёта по практике: выводы, полученный опыт, предложения.",
        pages=max(1, pages // 10),
    )
    sections.append("ЗАКЛЮЧЕНИЕ\n\n" + conclusion)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Список литературы
    bib_result = await chat_completion(
        messages=[
            {
                "role": "system",
                "content": "Составь список литературы для отчёта по практике. Формат: ГОСТ Р 7.0.5-2008.",
            },
            {
                "role": "user",
                "content": f"Составь список из 5-10 источников.\nТема: \"{title}\"\nПредмет: {subject}",
            },
        ],
        model=settings.openai_model_fast,
        temperature=0.4,
        max_tokens=1024,
    )
    sections.append("СПИСОК ЛИТЕРАТУРЫ\n\n" + bib_result["content"])
    total_input += bib_result["input_tokens"]
    total_output += bib_result["output_tokens"]
    total_cost += bib_result["cost_usd"]

    full_text = "\n\n".join(sections)
    pages_approx = max(1, len(full_text) // CHARS_PER_PAGE)

    logger.info(
        "Отчёт по практике сгенерирован: '%s', ~%d стр., %d+%d токенов, $%.4f",
        title[:50], pages_approx, total_input, total_output, total_cost,
    )

    return GenerationResult(
        text=full_text,
        title=title,
        work_type="Отчёт по практике",
        plan=plan,
        pages_approx=pages_approx,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_input + total_output,
        cost_usd=total_cost,
    )


async def _generate_plan(
    title: str, description: str, subject: str, pages: int, methodology_summary: Optional[str],
) -> tuple[ReportPlan, dict]:
    """Сгенерировать план отчёта по практике."""
    prompt_parts = [
        f"Составь план отчёта по практике на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Объём: {pages} страниц",
    ]
    if description:
        prompt_parts.append(f"Описание: {description}")
    if methodology_summary:
        prompt_parts.append(f"Из методички: {methodology_summary[:500]}")

    prompt_parts.append(
        '\nОтветь в JSON: {"title": "...", "chapters": [{"number": 1, "title": "Характеристика организации"}, '
        '{"number": 2, "title": "Описание выполненных работ"}, {"number": 3, "title": "Анализ опыта"}]}'
    )

    result = await chat_completion_json(
        messages=[
            {"role": "system", "content": "Ты составляешь планы отчётов по практике. Отвечай строго в JSON."},
            {"role": "user", "content": "\n".join(p for p in prompt_parts if p)},
        ],
        model=settings.openai_model_fast,
        temperature=0.3,
        max_tokens=1024,
    )

    data = result["data"]
    plan = ReportPlan(
        title=data.get("title", title),
        chapters=data.get("chapters", []),
    )

    return plan, {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }


async def _generate_section(
    title: str, subject: str, plan_text: str, instruction: str,
    pages: int = 3, methodology_summary: Optional[str] = None,
) -> tuple[str, dict]:
    """Сгенерировать одну секцию отчёта."""
    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(1500, target_chars // 3))

    user_parts = [
        f"Тема практики: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"\nПлан отчёта:\n{plan_text}",
        f"\nЗадание: {instruction}",
        f"Объём: ~{target_chars} символов ({pages} стр.)",
    ]
    if methodology_summary:
        user_parts.append(f"\nИз методички: {methodology_summary[:500]}")

    result = await chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(p for p in user_parts if p)},
        ],
        model=settings.openai_model_main,
        temperature=0.7,
        max_tokens=max_tokens,
    )

    return result["content"], {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }


def _plan_to_text(plan: ReportPlan) -> str:
    """Преобразовать план в текст."""
    lines = ["Введение"]
    for ch in plan.chapters:
        lines.append(f"{ch.get('number', '')}. {ch.get('title', '')}")
    lines.append("Заключение")
    lines.append("Список литературы")
    return "\n".join(lines)
