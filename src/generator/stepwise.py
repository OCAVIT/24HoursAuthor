"""Общая утилита для пошаговой генерации: план → разделы → расширение до целевого объёма.

Все генераторы используют этот модуль для единообразной генерации:
1. GPT генерирует план (JSON — список разделов с целевым объёмом в словах)
2. Каждый раздел генерируется отдельным API-вызовом
3. В контекст передаётся краткое суммари предыдущих разделов
4. После склейки проверяется объём, при нехватке — расширяется самый короткий раздел
5. Библиография генерируется ПОСЛЕДНЕЙ с учётом цитируемых авторов из текста
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings
from src.docgen.formatter import strip_markdown, normalize_text

logger = logging.getLogger(__name__)

CHARS_PER_PAGE = 1800
WORDS_PER_PAGE = 250
MAX_EXPAND_ITERATIONS = 5


@dataclass
class SectionPlan:
    """Один раздел из плана."""
    name: str
    target_words: int


@dataclass
class GeneratedSection:
    """Сгенерированный раздел."""
    name: str
    text: str
    target_words: int


@dataclass
class StepwiseResult:
    """Результат пошаговой генерации."""
    text: str
    sections: list[GeneratedSection] = field(default_factory=list)
    plan: list[SectionPlan] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


def _accumulate(result: StepwiseResult, tokens_info: dict):
    """Добавить токены из API вызова к общему результату."""
    result.input_tokens += tokens_info.get("input_tokens", 0)
    result.output_tokens += tokens_info.get("output_tokens", 0)
    result.cost_usd += tokens_info.get("cost_usd", 0.0)
    result.total_tokens = result.input_tokens + result.output_tokens


# ---------------------------------------------------------------------------
# Helpers: определяем тип раздела для контекстно-зависимых инструкций
# ---------------------------------------------------------------------------

def _is_bibliography(section: SectionPlan) -> bool:
    """Список литературы / библиография."""
    name_lower = section.name.lower()
    return "литератур" in name_lower or "библиограф" in name_lower or "источник" in name_lower


def _is_conclusion(section: SectionPlan) -> bool:
    """Заключение."""
    return "заключени" in section.name.lower()


def _is_chapter_intro(section: SectionPlan, plan: list[SectionPlan], idx: int) -> bool:
    """Глава, за которой идут подразделы (1.1, 1.2, ...) — нужен только краткий вводный абзац."""
    name_lower = section.name.lower()
    if "глава" not in name_lower:
        return False
    # Проверяем: следующий раздел — подраздел? (начинается с цифры.цифра)
    if idx + 1 < len(plan):
        next_name = plan[idx + 1].name.strip()
        if re.match(r'^\d+\.\d+', next_name):
            return True
    return False


def _extract_cited_authors(sections: list[GeneratedSection]) -> list[str]:
    """Извлечь фамилии авторов, упомянутых в тексте, для согласования с библиографией."""
    all_text = " ".join(
        s.text for s in sections
        if not _is_bibliography(SectionPlan(name=s.name, target_words=0))
    )
    authors = set()
    # Зарубежные авторы: Tomlinson (2001), Hattie (2009)
    for m in re.finditer(r'([A-Z][a-z]{2,})\s*\(\d{4}\)', all_text):
        authors.add(m.group(1))
    # Русские авторы: Выготский (1978), А. К. Маркова (1983)
    for m in re.finditer(r'([А-ЯЁ][а-яё]{2,}(?:ой|ого|ину|ина|ова|ева|ёва|ый|ий)?)\s*\(\d{4}\)', all_text):
        authors.add(m.group(1))
    # Инициалы + фамилия: по мнению А. К. Маркова
    for m in re.finditer(r'[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?([А-ЯЁ][а-яё]{2,})', all_text):
        authors.add(m.group(1))
    return sorted(authors)


# ---------------------------------------------------------------------------
# Основные функции генерации
# ---------------------------------------------------------------------------

def clean_section_text(name: str, text: str) -> str:
    """Постобработка текста раздела: убрать markdown и дублирующийся заголовок."""
    # Убрать markdown-разметку
    text = strip_markdown(text)

    # Убрать дублирование заголовка раздела в начале текста.
    # GPT часто начинает с заголовка вроде "Введение" или "1.1 Подраздел",
    # а assemble_text() добавляет заголовок автоматически.
    lines = text.split("\n", 1)
    if lines:
        first_line = lines[0].strip()
        # Нормализуем для сравнения: убираем нумерацию, точки, пробелы
        name_clean = re.sub(r"^[\d.\s]+", "", name).strip().lower()
        first_clean = re.sub(r"^[\d.\s]+", "", first_line).strip().lower()
        # Убираем первую строку если она совпадает с заголовком раздела
        if name_clean and first_clean and (
            name_clean == first_clean
            or first_clean.startswith(name_clean)
            or name_clean.startswith(first_clean)
        ):
            text = lines[1].lstrip("\n") if len(lines) > 1 else ""

    # Нормализация пробелов и переносов
    text = normalize_text(text)
    return text


async def generate_plan(
    work_type: str,
    title: str,
    description: str,
    subject: str,
    total_pages: int,
    plan_instructions: str = "",
    methodology_summary: Optional[str] = None,
) -> tuple[list[SectionPlan], dict]:
    """Шаг 1: GPT генерирует план — JSON со списком разделов и целевым объёмом в словах."""
    total_words = total_pages * WORDS_PER_PAGE

    prompt_parts = [
        f"Составь план для работы типа '{work_type}' на тему: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"Общий требуемый объём: {total_pages} страниц = {total_words} слов",
    ]
    if description:
        prompt_parts.append(f"Описание/ТЗ заказчика: {description[:500]}")
    if methodology_summary:
        prompt_parts.append(f"Из методички: {methodology_summary[:500]}")
    if plan_instructions:
        prompt_parts.append(plan_instructions)

    prompt_parts.append(
        '\nВерни строго JSON: {"sections": [{"name": "Название раздела", "target_words": 500}, ...]}'
        f"\nВАЖНО: сумма target_words всех разделов должна быть >= {total_words}."
        "\nКаждый раздел будет генерироваться отдельным запросом, поэтому дели на логические блоки."
        "\nЕсли есть главы с подразделами — указывай и главу (как вводный блок на 100-150 слов), и каждый подраздел отдельно."
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
    raw_sections = data.get("sections", [])

    sections = [
        SectionPlan(
            name=s.get("name", f"Раздел {i + 1}"),
            target_words=s.get("target_words", 300),
        )
        for i, s in enumerate(raw_sections)
    ]

    # Фолбэк: если план пустой — создать базовый
    if not sections:
        words_per = total_words // 3
        sections = [
            SectionPlan(name="Введение", target_words=words_per),
            SectionPlan(name="Основная часть", target_words=words_per),
            SectionPlan(name="Заключение", target_words=words_per),
        ]

    tokens_info = {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }
    return sections, tokens_info


def format_plan(sections: list[SectionPlan]) -> str:
    """Отформатировать план в текстовый вид для контекста."""
    lines = []
    for i, s in enumerate(sections, 1):
        lines.append(f"{i}. {s.name} (~{s.target_words} слов)")
    return "\n".join(lines)


async def generate_section(
    section: SectionPlan,
    title: str,
    subject: str,
    plan_text: str,
    system_prompt: str,
    previous_summaries: list[str],
    methodology_summary: Optional[str] = None,
    temperature: float = 0.7,
    required_uniqueness: Optional[int] = None,
    extra_instructions: str = "",
    cited_authors: Optional[list[str]] = None,
) -> tuple[GeneratedSection, dict]:
    """Шаг 2: Генерация одного раздела."""
    target_chars = section.target_words * 7  # ~7 символов на русское слово с пробелами
    max_tokens = min(16000, max(1500, target_chars // 3))

    user_parts = [
        f"Тема работы: \"{title}\"",
        f"Предмет: {subject}" if subject else "",
        f"\nПлан работы:\n{plan_text}",
        f"\nНапиши раздел: {section.name}",
        f"Целевой объём раздела: {section.target_words} слов (~{target_chars} символов).",
        "\nТРЕБОВАНИЯ К КАЧЕСТВУ:"
        "\n- Пиши академическим стилем без воды. Каждый абзац должен содержать конкретику: факты, цифры, примеры, ссылки на источники."
        "\n- Не повторяй одну мысль разными словами."
        "\n- Если упоминаешь конкретные организации, события, даты, статистику — используй только достоверные данные, не выдумывай."
        "\n- Если в теме работы подразумеваются примеры (компании, страны, исследования) — приводи общеизвестные, легко проверяемые."
        "\n- Каждый тезис подкрепляй аргументом или примером."
        "\n- НЕ используй фразы: «таким образом», «необходимо отметить», «следует подчеркнуть», «стоит отметить», «в заключение следует отметить», «не менее важным является», «особое внимание следует уделить»."
        "\n- НЕ заканчивай раздел обобщающим абзацем-выводом (кроме конца главы).",
        "\nВАЖНО:"
        "\n- НЕ начинай текст с заголовка раздела — заголовок добавляется автоматически."
        "\n- НЕ используй markdown-разметку (символы #, ##, **, *, >, ```, -)."
        "\n- Пиши только чистый текст абзацами, без форматирования.",
    ]

    # Специальные инструкции для библиографии
    if _is_bibliography(section):
        authors_hint = ""
        if cited_authors:
            authors_hint = (
                f"\nВ тексте работы упоминались следующие авторы: {', '.join(cited_authors)}."
                "\nОБЯЗАТЕЛЬНО включи в список литературы всех этих авторов с реальными работами."
            )
        user_parts.append(
            "\nСТРОГИЕ ТРЕБОВАНИЯ К СПИСКУ ЛИТЕРАТУРЫ:"
            "\n- Оформление СТРОГО по ГОСТ Р 7.0.5-2008."
            "\n- Каждый источник — ОДНА СТРОКА. БЕЗ аннотаций, описаний и комментариев после выходных данных."
            "\n- Формат: Автор, И. О. Название работы / И. О. Автор. — Город: Издательство, Год. — Страниц с."
            "\n- Для зарубежных: Author, A. B. Title / A. B. Author. — City: Publisher, Year. — Pages p."
            "\n- Расположи в алфавитном порядке: сначала русскоязычные, затем иностранные."
            "\n- НЕ выдумывай несуществующих книг — используй реальные, широко известные работы."
            "\n- Каждая строка начинается с порядкового номера: 1. ..., 2. ..., и т.д."
            f"{authors_hint}"
        )
    # Инструкции для вводного абзаца главы или заключения
    elif extra_instructions:
        user_parts.append(extra_instructions)

    if previous_summaries:
        summaries_text = "\n".join(previous_summaries[-3:])
        user_parts.append(f"\nКраткое содержание предыдущих разделов:\n{summaries_text}")

    if methodology_summary:
        user_parts.append(f"\nИз методички: {methodology_summary[:500]}")

    if required_uniqueness:
        user_parts.append(
            f"\nТребуемая уникальность: {required_uniqueness}%. "
            "Пиши максимально оригинальным языком, избегай клише и шаблонных фраз."
        )

    result = await chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(p for p in user_parts if p)},
        ],
        model=settings.openai_model_main,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    text = clean_section_text(section.name, result["content"])
    generated = GeneratedSection(name=section.name, text=text, target_words=section.target_words)

    tokens_info = {
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }
    return generated, tokens_info


def make_summary(section: GeneratedSection) -> str:
    """Краткое суммари раздела для контекста следующих разделов (экономим токены)."""
    text = section.text
    if len(text) > 200:
        return f"{section.name}: {text[:200]}..."
    return f"{section.name}: {text}"


async def expand_to_target(
    sections: list[GeneratedSection],
    target_chars: int,
    title: str,
    system_prompt: str,
    temperature: float = 0.7,
) -> tuple[list[GeneratedSection], dict]:
    """Шаг 3: Расширение до целевого объёма.

    Если суммарный объём < target_chars, дозапрашиваем самый короткий раздел
    с промптом 'Расширь этот раздел, добавь примеры, детали и аргументацию, нужно ещё N слов'.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for iteration in range(MAX_EXPAND_ITERATIONS):
        total_chars = sum(len(s.text) for s in sections)
        if total_chars >= target_chars:
            break

        needed_chars = target_chars - total_chars
        needed_words = max(50, needed_chars // 7)

        # Не расширяем список литературы, заключение и вводные абзацы глав
        expandable = [
            (i, s) for i, s in enumerate(sections)
            if not _is_bibliography(SectionPlan(name=s.name, target_words=0))
            and not _is_conclusion(SectionPlan(name=s.name, target_words=0))
            and "глава" not in s.name.lower().split(".")[0].strip()
        ]
        if not expandable:
            expandable = [
                (i, s) for i, s in enumerate(sections)
                if not _is_bibliography(SectionPlan(name=s.name, target_words=0))
            ]
        if not expandable:
            expandable = list(enumerate(sections))

        shortest_idx, shortest = min(expandable, key=lambda x: len(x[1].text))

        max_tokens = min(16000, max(1500, needed_chars // 3))

        logger.info(
            "Расширение '%s' (итерация %d): нужно ещё %d слов (%d/%d символов)",
            shortest.name, iteration + 1, needed_words, total_chars, target_chars,
        )

        result = await chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"Тема работы: \"{title}\"\n"
                    f"Раздел: {shortest.name}\n\n"
                    f"Текущий текст раздела:\n{shortest.text}\n\n"
                    f"Расширь этот раздел, добавь новые примеры, детали и аргументацию, "
                    f"нужно ещё {needed_words} слов. "
                    f"Верни ПОЛНЫЙ текст раздела (старый + новый контент, без повторов)."
                    f"\n\nВАЖНО:"
                    f"\n- НЕ добавляй заголовок раздела — он добавляется автоматически."
                    f"\n- НЕ используй markdown-разметку (символы #, ##, **, *, >, ```, -)."
                    f"\n- Пиши только чистый текст абзацами."
                    f"\n- Добавляй НОВУЮ информацию, не перефразируй существующую."
                    f"\n- НЕ используй фразы: «таким образом», «необходимо отметить», «стоит отметить»."
                )},
            ],
            model=settings.openai_model_main,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        sections[shortest_idx] = GeneratedSection(
            name=shortest.name,
            text=clean_section_text(shortest.name, result["content"]),
            target_words=shortest.target_words,
        )
        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_cost += result["cost_usd"]

    tokens_info = {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": total_cost,
    }
    return sections, tokens_info


def assemble_text(sections: list[GeneratedSection], uppercase_names: bool = True) -> str:
    """Собрать финальный текст из сгенерированных разделов."""
    parts = []
    for s in sections:
        heading = s.name.upper() if uppercase_names else s.name
        text = clean_section_text(s.name, s.text)
        parts.append(f"{heading}\n\n{text}")
    return normalize_text("\n\n".join(parts))


async def stepwise_generate(
    work_type: str,
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 10,
    system_prompt: str = "",
    plan_instructions: str = "",
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    temperature: float = 0.7,
    uppercase_headings: bool = True,
) -> StepwiseResult:
    """Полный пайплайн пошаговой генерации: план → разделы → расширение.

    1. GPT-4o-mini генерирует план (JSON: разделы + target_words)
    2. Каждый раздел генерируется GPT-4o, с суммари предыдущих как контекст
    3. Библиография генерируется ПОСЛЕДНЕЙ с учётом цитируемых авторов
    4. После склейки проверяется общий объём, при нехватке — расширяется самый короткий
    """
    target_chars = pages * CHARS_PER_PAGE

    sw = StepwiseResult(text="")

    # Шаг 1: Генерация плана
    plan, tokens_info = await generate_plan(
        work_type=work_type,
        title=title,
        description=description,
        subject=subject,
        total_pages=pages,
        plan_instructions=plan_instructions,
        methodology_summary=methodology_summary,
    )
    sw.plan = plan
    _accumulate(sw, tokens_info)

    plan_text = format_plan(plan)

    # Разделяем план: контентные разделы и библиография
    content_plan: list[SectionPlan] = []
    bib_plan: list[SectionPlan] = []
    for section in plan:
        if _is_bibliography(section):
            bib_plan.append(section)
        else:
            content_plan.append(section)

    # Шаг 2: Генерация контентных разделов
    previous_summaries: list[str] = []

    for idx, section in enumerate(content_plan):
        # Определяем дополнительные инструкции по типу раздела
        extra = ""
        if _is_chapter_intro(section, content_plan, idx):
            extra = (
                "\nЭТОТ РАЗДЕЛ — ВВОДНАЯ ЧАСТЬ ГЛАВЫ (не подраздел)."
                "\nНапиши РОВНО 1-2 абзаца (100-150 слов):"
                "\n- Кратко обозначь, какие вопросы рассматриваются в подразделах этой главы."
                "\n- НЕ раскрывай содержание подробно — оно будет написано в подразделах."
                "\n- НЕ давай определения, классификации, примеры — это материал подразделов."
                "\nПревышение объёма приведёт к дублированию с подразделами."
            )
        elif _is_conclusion(section):
            extra = (
                "\nЗАКЛЮЧЕНИЕ должно быть КРАТКИМ (2-3 страницы, 500-750 слов максимум)."
                "\nСтруктура заключения:"
                "\n- По 2-3 предложения с ключевым выводом по КАЖДОЙ главе."
                "\n- Оценка степени достижения цели исследования."
                "\n- Практическая значимость результатов (2-3 предложения)."
                "\n- 1-2 рекомендации или перспективы дальнейших исследований."
                "\nНЕ пересказывай содержание работы. НЕ вводи новую информацию. Только выводы."
                "\nНЕ повторяй формулировки из текста работы — перефразируй."
            )

        generated, tokens_info = await generate_section(
            section=section,
            title=title,
            subject=subject,
            plan_text=plan_text,
            system_prompt=system_prompt,
            previous_summaries=previous_summaries,
            methodology_summary=methodology_summary,
            temperature=temperature,
            required_uniqueness=required_uniqueness,
            extra_instructions=extra,
        )
        sw.sections.append(generated)
        _accumulate(sw, tokens_info)

        previous_summaries.append(make_summary(generated))

    # Шаг 2.5: Генерация библиографии (если есть) с учётом цитируемых авторов
    if bib_plan:
        cited_authors = _extract_cited_authors(sw.sections)
        logger.info("Найдено %d цитируемых авторов для библиографии: %s",
                     len(cited_authors), ", ".join(cited_authors[:10]))

        for section in bib_plan:
            generated, tokens_info = await generate_section(
                section=section,
                title=title,
                subject=subject,
                plan_text=plan_text,
                system_prompt=system_prompt,
                previous_summaries=previous_summaries,
                methodology_summary=methodology_summary,
                temperature=0.3,  # Низкая температура для точности библиографии
                required_uniqueness=required_uniqueness,
                cited_authors=cited_authors,
            )
            sw.sections.append(generated)
            _accumulate(sw, tokens_info)

    # Шаг 3: Расширение до целевого объёма
    sw.sections, tokens_info = await expand_to_target(
        sections=sw.sections,
        target_chars=target_chars,
        title=title,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    _accumulate(sw, tokens_info)

    # Собираем финальный текст
    sw.text = assemble_text(sw.sections, uppercase_names=uppercase_headings)

    return sw
