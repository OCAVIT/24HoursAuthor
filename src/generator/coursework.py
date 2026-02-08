"""Генератор курсовых работ (пошаговая генерация по главам)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "coursework_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class CourseworkPlan:
    """Оглавление курсовой работы."""
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
    pages: int = 30,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать курсовую работу пошагово: план -> введение -> главы -> заключение -> литература."""
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

    # Введение (2-3 страницы)
    intro_pages = max(2, pages // 10)
    intro, t = await _generate_section(
        title, subject, plan, "introduction",
        (
            "Напиши введение курсовой работы: актуальность темы, степень изученности проблемы, "
            "цель работы, задачи, объект и предмет исследования, методы исследования, "
            "краткое описание структуры работы."
        ),
        pages=intro_pages,
        methodology_summary=methodology_summary,
    )
    sections.append("ВВЕДЕНИЕ\n\n" + intro)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Главы
    available_pages = pages - intro_pages - 3 - 2  # минус введение, заключение, литература
    chapter_pages = max(5, available_pages // max(1, len(plan.chapters)))
    previous_summaries: list[str] = []

    for chapter in plan.chapters:
        ch_number = chapter.get("number", "")
        ch_title = chapter.get("title", "")
        subsections = chapter.get("subsections", [])

        # Генерация каждого подраздела отдельно, если глава большая
        if len(subsections) > 1 and chapter_pages > 8:
            chapter_text = await _generate_chapter_by_subsections(
                title, subject, plan, chapter, chapter_pages,
                previous_summaries, methodology_summary,
            )
            # Подсчитываем токены (уже учтены внутри)
            sub_pages = max(2, chapter_pages // len(subsections))
            for sub in subsections:
                sub_text, t = await _generate_section(
                    title, subject, plan, "subsection",
                    f"Напиши подраздел '{sub}' главы '{ch_title}'.",
                    pages=sub_pages,
                    methodology_summary=methodology_summary,
                    extra_context="\n".join(previous_summaries[-2:]) if previous_summaries else None,
                )
                total_input += t["input_tokens"]
                total_output += t["output_tokens"]
                total_cost += t["cost_usd"]

            # Собираем главу
            chapter_intro, t = await _generate_section(
                title, subject, plan, "chapter_intro",
                f"Напиши вводный абзац к главе {ch_number} '{ch_title}' курсовой работы (2-3 предложения).",
                pages=1,
            )
            total_input += t["input_tokens"]
            total_output += t["output_tokens"]
            total_cost += t["cost_usd"]

            chapter_conclusion, t = await _generate_section(
                title, subject, plan, "chapter_conclusion",
                f"Напиши выводы по главе {ch_number} '{ch_title}' (1 абзац, 4-5 предложений).",
                pages=1,
                extra_context="\n".join(previous_summaries[-2:]) if previous_summaries else None,
            )
            total_input += t["input_tokens"]
            total_output += t["output_tokens"]
            total_cost += t["cost_usd"]
        else:
            # Генерация всей главы одним вызовом
            context = f"Глава {ch_number}: {ch_title}"
            if subsections:
                context += "\nПодразделы: " + ", ".join(subsections)
            if previous_summaries:
                context += "\n\nКраткое содержание предыдущих глав:\n" + "\n".join(previous_summaries[-2:])

            chapter_text, t = await _generate_section(
                title, subject, plan, "chapter",
                f"Напиши {context}.\nОбъём: {chapter_pages} страниц. "
                f"Включи вводный абзац к главе и промежуточные выводы в конце.",
                pages=chapter_pages,
                methodology_summary=methodology_summary,
            )
            total_input += t["input_tokens"]
            total_output += t["output_tokens"]
            total_cost += t["cost_usd"]

        sections.append(f"ГЛАВА {ch_number}. {ch_title.upper()}\n\n{chapter_text}")

        summary = chapter_text[:400] + "..." if len(chapter_text) > 400 else chapter_text
        previous_summaries.append(f"Глава {ch_number} '{ch_title}': {summary}")

    # Заключение (2-3 страницы)
    conclusion_pages = max(2, pages // 10)
    conclusion, t = await _generate_section(
        title, subject, plan, "conclusion",
        (
            "Напиши заключение курсовой работы: основные выводы по каждой главе, "
            "степень достижения цели, практическая значимость результатов, "
            "рекомендации и перспективы дальнейшего исследования."
        ),
        pages=conclusion_pages,
        extra_context="\n".join(previous_summaries),
    )
    sections.append("ЗАКЛЮЧЕНИЕ\n\n" + conclusion)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Список литературы (15-25 источников)
    bibliography, t = await _generate_bibliography(title, subject, plan)
    sections.append(bibliography)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    full_text = "\n\n".join(sections)
    pages_approx = max(1, len(full_text) // CHARS_PER_PAGE)

    logger.info(
        "Курсовая сгенерирована: '%s', ~%d стр., %d+%d токенов, $%.4f",
        title[:50], pages_approx, total_input, total_output, total_cost,
    )

    return GenerationResult(
        text=full_text,
        title=title,
        work_type="Курсовая работа",
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
) -> tuple[CourseworkPlan, dict]:
    """Сгенерировать план курсовой работы через GPT-4o-mini."""
    prompt_parts = [
        f"Составь план курсовой работы на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Объём: {pages} страниц",
        f"Количество глав: 2-3, в каждой 2-3 подраздела",
    ]
    if description:
        prompt_parts.append(f"Описание: {description}")
    if methodology_summary:
        prompt_parts.append(f"Из методички: {methodology_summary[:500]}")

    prompt_parts.append(
        '\nОтветь в JSON: {"title": "...", "chapters": [{"number": 1, "title": "...", '
        '"subsections": ["1.1 ...", "1.2 ...", "1.3 ..."]}]}'
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
    plan = CourseworkPlan(
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
    plan: CourseworkPlan,
    section_type: str,
    instruction: str,
    pages: int = 5,
    methodology_summary: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> tuple[str, dict]:
    """Сгенерировать одну секцию курсовой работы."""
    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(1500, target_chars // 3))

    plan_text = _plan_to_text(plan)

    user_parts = [
        f"Тема курсовой работы: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"\nПлан курсовой работы:\n{plan_text}",
        f"\nЗадание: {instruction}",
        f"Объём секции: ~{target_chars} символов ({pages} стр.)",
    ]

    if methodology_summary:
        user_parts.append(f"\nИз методички: {methodology_summary[:500]}")
    if extra_context:
        user_parts.append(f"\nКонтекст:\n{extra_context[:1500]}")

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


async def _generate_chapter_by_subsections(
    title: str,
    subject: str,
    plan: CourseworkPlan,
    chapter: dict,
    total_pages: int,
    previous_summaries: list[str],
    methodology_summary: Optional[str],
) -> str:
    """Сгенерировать главу по подразделам (для больших глав)."""
    subsections = chapter.get("subsections", [])
    if not subsections:
        return ""

    sub_pages = max(2, total_pages // len(subsections))
    texts: list[str] = []

    for sub in subsections:
        text, _ = await _generate_section(
            title, subject, plan, "subsection",
            f"Напиши подраздел '{sub}' главы '{chapter.get('title', '')}'.",
            pages=sub_pages,
            methodology_summary=methodology_summary,
            extra_context="\n".join(previous_summaries[-2:]) if previous_summaries else None,
        )
        texts.append(f"{sub}\n\n{text}")

    return "\n\n".join(texts)


async def _generate_bibliography(
    title: str,
    subject: str,
    plan: CourseworkPlan,
) -> tuple[str, dict]:
    """Сгенерировать список литературы для курсовой."""
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
                    f"Составь список из 15-25 источников для курсовой работы.\n"
                    f"Тема: \"{title}\"\n"
                    f"Предмет: {subject}\n"
                    f"Главы: {chapters_text}\n\n"
                    f"Включи:\n"
                    f"- 5-7 учебников (реальные авторы, реальные издательства)\n"
                    f"- 3-5 статей из журналов\n"
                    f"- 3-5 нормативных документов (если тема правовая/экономическая)\n"
                    f"- 2-3 интернет-источника\n\n"
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


def _plan_to_text(plan: CourseworkPlan) -> str:
    """Преобразовать план в текстовый формат."""
    lines = ["Введение"]
    for ch in plan.chapters:
        lines.append(f"Глава {ch.get('number', '')}. {ch.get('title', '')}")
        for sub in ch.get("subsections", []):
            lines.append(f"  {sub}")
    lines.append("Заключение")
    lines.append("Список литературы")
    return "\n".join(lines)
