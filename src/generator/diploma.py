"""Генератор дипломных работ и ВКР (пошаговая генерация по главам)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "diploma_system.txt").read_text(encoding="utf-8")

CHARS_PER_PAGE = 1800


@dataclass
class DiplomaPlan:
    """Оглавление дипломной работы."""
    title: str
    introduction: bool = True
    chapters: list[dict] = field(default_factory=list)
    conclusion: bool = True
    bibliography: bool = True
    appendices: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    """Результат генерации дипломной работы."""
    text: str
    title: str
    work_type: str
    plan: Optional[DiplomaPlan] = None
    pages_approx: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 80,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать дипломную работу/ВКР пошагово."""
    total_input = 0
    total_output = 0
    total_cost = 0.0

    # Шаг 1: Генерация плана
    plan, tokens_info = await _generate_plan(title, description, subject, pages, methodology_summary)
    total_input += tokens_info["input_tokens"]
    total_output += tokens_info["output_tokens"]
    total_cost += tokens_info["cost_usd"]

    sections: list[str] = []

    # Аннотация (1 страница)
    annotation, t = await _generate_section(
        title, subject, plan, "annotation",
        "Напиши аннотацию к ВКР: краткое описание работы, цель, методы, основные результаты (1 страница).",
        pages=1,
    )
    sections.append("АННОТАЦИЯ\n\n" + annotation)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Введение (3-5 страниц)
    intro_pages = max(3, pages // 20)
    intro, t = await _generate_section(
        title, subject, plan, "introduction",
        (
            "Напиши введение ВКР: актуальность темы, степень изученности проблемы, "
            "цель работы, задачи (4-6 задач), объект и предмет исследования, "
            "методологическая база, научная новизна, практическая значимость, "
            "описание структуры работы."
        ),
        pages=intro_pages,
        methodology_summary=methodology_summary,
    )
    sections.append("ВВЕДЕНИЕ\n\n" + intro)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Главы (каждая по 15-25 страниц)
    available_pages = pages - intro_pages - 4 - 3 - 1  # минус введение, заключение, литература, аннотация
    chapter_pages = max(10, available_pages // max(1, len(plan.chapters)))
    previous_summaries: list[str] = []

    for chapter in plan.chapters:
        ch_number = chapter.get("number", "")
        ch_title = chapter.get("title", "")
        subsections = chapter.get("subsections", [])

        # Для дипломной: генерация каждого подраздела отдельным вызовом
        chapter_parts: list[str] = []

        # Вводный абзац главы
        ch_intro, t = await _generate_section(
            title, subject, plan, "chapter_intro",
            f"Напиши вводный абзац к главе {ch_number} '{ch_title}' дипломной работы (3-4 предложения).",
            pages=1,
        )
        chapter_parts.append(ch_intro)
        total_input += t["input_tokens"]
        total_output += t["output_tokens"]
        total_cost += t["cost_usd"]

        # Подразделы
        sub_pages = max(3, chapter_pages // max(1, len(subsections))) if subsections else chapter_pages
        for sub in subsections:
            sub_text, t = await _generate_section(
                title, subject, plan, "subsection",
                f"Напиши подраздел '{sub}' главы {ch_number} '{ch_title}'.\nОбъём: {sub_pages} страниц.",
                pages=sub_pages,
                methodology_summary=methodology_summary,
                extra_context="\n".join(previous_summaries[-3:]) if previous_summaries else None,
            )
            chapter_parts.append(f"{sub}\n\n{sub_text}")
            total_input += t["input_tokens"]
            total_output += t["output_tokens"]
            total_cost += t["cost_usd"]

        # Выводы по главе
        ch_conclusion, t = await _generate_section(
            title, subject, plan, "chapter_conclusion",
            f"Напиши выводы по главе {ch_number} '{ch_title}' (1-2 абзаца, основные результаты и промежуточные выводы).",
            pages=1,
            extra_context="\n\n".join(chapter_parts[-2:])[:1000],
        )
        chapter_parts.append(f"Выводы по главе {ch_number}\n\n{ch_conclusion}")
        total_input += t["input_tokens"]
        total_output += t["output_tokens"]
        total_cost += t["cost_usd"]

        chapter_text = "\n\n".join(chapter_parts)
        sections.append(f"ГЛАВА {ch_number}. {ch_title.upper()}\n\n{chapter_text}")

        summary = chapter_text[:500] + "..." if len(chapter_text) > 500 else chapter_text
        previous_summaries.append(f"Глава {ch_number} '{ch_title}': {summary}")

    # Заключение (3-5 страниц)
    conclusion_pages = max(3, pages // 20)
    conclusion, t = await _generate_section(
        title, subject, plan, "conclusion",
        (
            "Напиши заключение ВКР: основные выводы по каждой главе, "
            "степень достижения цели и решения задач, "
            "практическая значимость полученных результатов, "
            "рекомендации и перспективы дальнейшего исследования."
        ),
        pages=conclusion_pages,
        extra_context="\n".join(previous_summaries),
    )
    sections.append("ЗАКЛЮЧЕНИЕ\n\n" + conclusion)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    # Список литературы (30-50 источников)
    bibliography, t = await _generate_bibliography(title, subject, plan)
    sections.append(bibliography)
    total_input += t["input_tokens"]
    total_output += t["output_tokens"]
    total_cost += t["cost_usd"]

    full_text = "\n\n".join(sections)
    pages_approx = max(1, len(full_text) // CHARS_PER_PAGE)

    logger.info(
        "ВКР сгенерирована: '%s', ~%d стр., %d+%d токенов, $%.4f",
        title[:50], pages_approx, total_input, total_output, total_cost,
    )

    return GenerationResult(
        text=full_text,
        title=title,
        work_type="Дипломная работа",
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
) -> tuple[DiplomaPlan, dict]:
    """Сгенерировать план ВКР через GPT-4o-mini."""
    prompt_parts = [
        f"Составь план выпускной квалификационной работы (ВКР) на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Объём: {pages} страниц",
        f"Количество глав: 3-4, в каждой 3-4 подраздела",
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
        max_tokens=1536,
    )

    data = result["data"]
    plan = DiplomaPlan(
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
    plan: DiplomaPlan,
    section_type: str,
    instruction: str,
    pages: int = 5,
    methodology_summary: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> tuple[str, dict]:
    """Сгенерировать одну секцию дипломной работы."""
    target_chars = pages * CHARS_PER_PAGE
    max_tokens = min(16000, max(1500, target_chars // 3))

    plan_text = _plan_to_text(plan)

    user_parts = [
        f"Тема ВКР: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"\nПлан ВКР:\n{plan_text}",
        f"\nЗадание: {instruction}",
        f"Объём секции: ~{target_chars} символов ({pages} стр.)",
    ]

    if methodology_summary:
        user_parts.append(f"\nИз методички: {methodology_summary[:500]}")
    if extra_context:
        user_parts.append(f"\nКонтекст:\n{extra_context[:2000]}")

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
    plan: DiplomaPlan,
) -> tuple[str, dict]:
    """Сгенерировать список литературы для ВКР (30-50 источников)."""
    chapters_text = ", ".join(ch.get("title", "") for ch in plan.chapters)

    result = await chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты составляешь списки литературы для академических работ. "
                    "Формат: ГОСТ Р 7.0.5-2008. Используй только реально существующих "
                    "авторов и реальные издательства."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Составь список из 30-50 источников для ВКР.\n"
                    f"Тема: \"{title}\"\n"
                    f"Предмет: {subject}\n"
                    f"Главы: {chapters_text}\n\n"
                    f"Включи:\n"
                    f"- 10-15 учебников и монографий\n"
                    f"- 5-8 статей из научных журналов\n"
                    f"- 5-7 нормативных документов (если тема правовая/экономическая)\n"
                    f"- 3-5 диссертаций и авторефератов\n"
                    f"- 3-5 интернет-источников\n"
                    f"- 2-3 зарубежных источника\n\n"
                    f"Оформи как нумерованный список. Каждый источник на отдельной строке."
                ),
            },
        ],
        model=settings.openai_model_fast,
        temperature=0.4,
        max_tokens=4096,
    )

    return "СПИСОК ЛИТЕРАТУРЫ\n\n" + result["content"], {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }


def _plan_to_text(plan: DiplomaPlan) -> str:
    """Преобразовать план в текстовый формат."""
    lines = ["Аннотация", "Введение"]
    for ch in plan.chapters:
        lines.append(f"Глава {ch.get('number', '')}. {ch.get('title', '')}")
        for sub in ch.get("subsections", []):
            lines.append(f"  {sub}")
    lines.append("Заключение")
    lines.append("Список литературы")
    if plan.appendices:
        for app in plan.appendices:
            lines.append(f"Приложение: {app}")
    return "\n".join(lines)
