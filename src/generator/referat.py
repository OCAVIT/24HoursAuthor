"""Генератор рефератов, докладов и статей."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "referat_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class ReferatPlan:
    """Оглавление реферата."""
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
    pages: int = 15,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать реферат пошагово: план → введение → главы → заключение → литература."""
    total_input = 0
    total_output = 0
    total_cost = 0.0

    # Шаг 1: Генерация плана
    plan, tokens_info = await _generate_plan(title, description, subject, pages, methodology_summary)
    total_input += tokens_info["input_tokens"]
    total_output += tokens_info["output_tokens"]
    total_cost += tokens_info["cost_usd"]

    # Шаг 2: Генерация секций
    sections: list[str] = []

    # Введение
    intro, t = await _generate_section(
        title, subject, plan, "introduction",
        "Напиши введение реферата (1-2 страницы): актуальность темы, цель, задачи, краткий обзор структуры.",
        pages=max(1, pages // 8),
        methodology_summary=methodology_summary,
    )
    sections.append(intro)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Главы
    chapter_pages = max(2, (pages - 4) // max(1, len(plan.chapters)))
    previous_summaries: list[str] = []

    for chapter in plan.chapters:
        ch_title = chapter.get("title", "")
        subsections = chapter.get("subsections", [])

        context = f"Глава {chapter.get('number', '')}: {ch_title}"
        if subsections:
            context += "\nПодразделы: " + ", ".join(subsections)
        if previous_summaries:
            context += "\n\nКраткое содержание предыдущих глав:\n" + "\n".join(previous_summaries[-2:])

        chapter_text, t = await _generate_section(
            title, subject, plan, "chapter",
            f"Напиши {context}.\nОбъём: {chapter_pages} страниц.",
            pages=chapter_pages,
            methodology_summary=methodology_summary,
        )
        sections.append(chapter_text)
        total_input += t["input_tokens"]
        total_output += t["output_tokens"]
        total_cost += t["cost_usd"]

        # Краткое summary для контекста следующих глав
        summary = chapter_text[:300] + "..." if len(chapter_text) > 300 else chapter_text
        previous_summaries.append(f"{ch_title}: {summary}")

    # Заключение
    conclusion, t = await _generate_section(
        title, subject, plan, "conclusion",
        "Напиши заключение реферата (1-2 страницы): выводы по каждой главе, общий итог.",
        pages=max(1, pages // 8),
        extra_context="\n".join(previous_summaries),
    )
    sections.append(conclusion)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Список литературы
    bibliography, t = await _generate_bibliography(title, subject, plan)
    sections.append(bibliography)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    full_text = "\n\n".join(sections)
    pages_approx = max(1, len(full_text) // CHARS_PER_PAGE)

    logger.info(
        "Реферат сгенерирован: '%s', ~%d стр., %d+%d токенов, $%.4f",
        title[:50], pages_approx, total_input, total_output, total_cost,
    )

    return GenerationResult(
        text=full_text,
        title=title,
        work_type="Реферат",
        plan=plan,
        pages_approx=pages_approx,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_input + total_output,
        cost_usd=total_cost,
    )


async def _generate_plan(
    title: str,
    description: str,
    subject: str,
    pages: int,
    methodology_summary: Optional[str],
) -> tuple[ReferatPlan, dict]:
    """Сгенерировать план реферата через GPT-4o-mini."""
    prompt_parts = [
        f"Составь план реферата на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Объём: {pages} страниц",
    ]
    if description:
        prompt_parts.append(f"Описание: {description}")
    if methodology_summary:
        prompt_parts.append(f"Из методички: {methodology_summary[:500]}")

    prompt_parts.append(
        '\nОтветь в JSON: {"title": "...", "chapters": [{"number": 1, "title": "...", '
        '"subsections": ["1.1 ...", "1.2 ..."]}]}'
    )

    result = await chat_completion_json(
        messages=[
            {"role": "system", "content": "Ты составляешь планы академических работ. Отвечай строго в JSON."},
            {"role": "user", "content": "\n".join(p for p in prompt_parts if p)},
        ],
        model=settings.openai_model_fast,
        temperature=0.3,
        max_tokens=1024,
    )

    data = result["data"]
    plan = ReferatPlan(
        title=data.get("title", title),
        chapters=data.get("chapters", []),
    )

    return plan, {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }


async def _generate_section(
    title: str,
    subject: str,
    plan: ReferatPlan,
    section_type: str,
    instruction: str,
    pages: int = 3,
    methodology_summary: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> tuple[str, dict]:
    """Сгенерировать одну секцию реферата."""
    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(1500, target_chars // 3))

    plan_text = _plan_to_text(plan)

    user_parts = [
        f"Тема реферата: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"\nПлан реферата:\n{plan_text}",
        f"\nЗадание: {instruction}",
        f"Объём секции: ~{target_chars} символов ({pages} стр.)",
    ]

    if methodology_summary:
        user_parts.append(f"\nИз методички: {methodology_summary[:500]}")
    if extra_context:
        user_parts.append(f"\nКонтекст:\n{extra_context[:1000]}")

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


async def _generate_bibliography(
    title: str,
    subject: str,
    plan: ReferatPlan,
) -> tuple[str, dict]:
    """Сгенерировать список литературы."""
    chapters_text = ", ".join(ch.get("title", "") for ch in plan.chapters)

    result = await chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты составляешь списки литературы для академических работ. "
                    "Формат: ГОСТ Р 7.0.5-2008. Используй только реально существующих "
                    "авторов и реальные издательства. Включи учебники, статьи из журналов, "
                    "нормативные документы (если тема правовая/экономическая) и интернет-источники."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Составь список из 10-15 источников для реферата.\n"
                    f"Тема: \"{title}\"\n"
                    f"Предмет: {subject}\n"
                    f"Главы: {chapters_text}\n\n"
                    f"Оформи как нумерованный список. Каждый источник на отдельной строке."
                ),
            },
        ],
        model=settings.openai_model_fast,
        temperature=0.4,
        max_tokens=2048,
    )

    return "СПИСОК ЛИТЕРАТУРЫ\n\n" + result["content"], {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }


def _plan_to_text(plan: ReferatPlan) -> str:
    """Преобразовать план в текстовый формат."""
    lines = ["Введение"]
    for ch in plan.chapters:
        lines.append(f"Глава {ch.get('number', '')}. {ch.get('title', '')}")
        for sub in ch.get("subsections", []):
            lines.append(f"  {sub}")
    lines.append("Заключение")
    lines.append("Список литературы")
    return "\n".join(lines)
