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
MAX_EXPAND_ITERATIONS = 12


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


def _is_introduction(section: SectionPlan) -> bool:
    """Введение."""
    return "введени" in section.name.lower()


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


def _extract_cited_references(sections: list[GeneratedSection]) -> list[str]:
    """Извлечь пары 'Автор (Год)' из текста для точного согласования с библиографией.

    Возвращает список строк вида 'Фамилия (Год)' — библиография должна содержать
    источники с ТОЧНО этими годами, чтобы не было расхождений.
    """
    all_text = " ".join(
        s.text for s in sections
        if not _is_bibliography(SectionPlan(name=s.name, target_words=0))
    )
    refs = set()
    # Зарубежные: Tomlinson (2001), Hattie (2009)
    for m in re.finditer(r'([A-Z][a-z]{2,})\s*\((\d{4})\)', all_text):
        refs.add(f"{m.group(1)} ({m.group(2)})")
    # Русские: Выготский (1934), Маркова (1983), Литвинова (2018)
    for m in re.finditer(r'([А-ЯЁ][а-яё]{2,}(?:ой|ого|ину|ина|ова|ева|ёва|ый|ий|а)?)\s*\((\d{4})\)', all_text):
        refs.add(f"{m.group(1)} ({m.group(2)})")
    # Инициалы + фамилия + год: А. К. Маркова (1983)
    for m in re.finditer(r'[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?([А-ЯЁ][а-яё]{2,})\s*\((\d{4})\)', all_text):
        refs.add(f"{m.group(1)} ({m.group(2)})")
    return sorted(refs)


# ---------------------------------------------------------------------------
# Основные функции генерации
# ---------------------------------------------------------------------------

_BANNED_PHRASES_MAP = {
    # --- Классические AI-маркеры ---
    "таким образом,": "",
    "таким образом ": "",
    "стоит отметить, что ": "",
    "стоит отметить,": "",
    "стоит отметить ": "",
    "стоит подчеркнуть, что ": "",
    "стоит подчеркнуть,": "",
    "стоит подчеркнуть ": "",
    "необходимо отметить, что ": "",
    "необходимо отметить,": "",
    "необходимо отметить ": "",
    "следует подчеркнуть, что ": "",
    "следует подчеркнуть,": "",
    "следует подчеркнуть ": "",
    "следует отметить, что ": "",
    "следует отметить,": "",
    "следует отметить ": "",
    "не менее важным является ": "",
    "особое внимание следует уделить ": "",
    "важно понимать, что ": "",
    "нельзя не отметить, что ": "",
    "нельзя не отметить,": "",
    "нельзя не отметить ": "",
    "подводя итоги,": "",
    "подводя итоги ": "",
    "на основании вышеизложенного,": "",
    "на основании вышеизложенного ": "",
    "представляется целесообразным ": "",
    "в данном контексте ": "",
    "в данном контексте,": "",
    "в свою очередь,": "",
    "в свою очередь ": "",
    "в связи с этим,": "",
    "в связи с этим ": "",
    "кроме того, стоит подчеркнуть,": "",
    "кроме того, стоит подчеркнуть ": "",
    # --- Обобщающие концовки разделов ---
    "в заключение следует отметить, что ": "",
    "в заключение следует отметить,": "",
    "в заключение можно сказать, что ": "",
    "в заключение можно сказать,": "",
    "в заключение можно отметить, что ": "",
    "в заключение можно отметить,": "",
    "в заключение можно констатировать, что ": "",
    "в заключение можно констатировать,": "",
    "в заключение данного ": "",
    # --- Анонимные исследования ---
    "исследования показывают, что ": "",
    "исследования показывают ": "",
    "исследования свидетельствуют, что ": "",
    "исследования свидетельствуют о том, что ": "",
    "исследования свидетельствуют о ": "",
    "по данным исследований,": "",
    "по данным исследований ": "",
    "по данным многочисленных исследований,": "",
    "по данным многочисленных исследований ": "",
    "учёные доказали, что ": "",
    "учёные установили, что ": "",
    "учёные показали, что ": "",
    "исследователи полагают, что ": "",
    "исследователи установили, что ": "",
    "исследователи отмечают, что ": "",
    "как показывают исследования,": "",
    "как показывают исследования ": "",
    "как свидетельствуют исследования,": "",
    "как свидетельствуют исследования ": "",
    "многочисленные исследования подтверждают, что ": "",
    "многочисленные исследования показывают, что ": "",
}

# Compile once for performance
_BANNED_PATTERN = re.compile(
    "|".join(re.escape(phrase) for phrase in _BANNED_PHRASES_MAP),
    re.IGNORECASE,
)


def _remove_banned_phrases(text: str) -> str:
    """Постпроцессинг: удалить запрещённые AI-фразы из текста.

    После удаления фразы в начале предложения — капитализируем следующее слово.
    """
    def _replace(match: re.Match) -> str:
        return ""

    result = _BANNED_PATTERN.sub(_replace, text)

    # Починить капитализацию после удаления фразы в начале предложения:
    # ". следующее слово" -> ". Следующее слово"
    result = re.sub(r'([.!?]\s+)([а-яё])', lambda m: m.group(1) + m.group(2).upper(), result)
    # Убрать двойные пробелы
    result = re.sub(r'  +', ' ', result)
    # Убрать пробел после начала абзаца + капитализировать
    result = re.sub(r'\n\s+([а-яё])', lambda m: '\n' + m.group(1).upper(), result)
    return result


def _dedup_paragraphs(text: str, threshold: float = 0.28) -> str:
    """Удалить абзацы, которые дублируют ранее написанные (по 3-грамному перекрытию).

    Решает проблему «два AI-текста склеены»: когда expand_to_target дописывает
    абзацы, повторяющие темы из первоначальной генерации.
    """
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip() and len(p.strip()) > 80]
    if len(paragraphs) < 3:
        return text

    def _trigrams(t: str) -> set:
        words = re.findall(r'[а-яёa-z]{3,}', t.lower())
        if len(words) < 3:
            return set()
        return {(words[i], words[i + 1], words[i + 2]) for i in range(len(words) - 2)}

    kept = [paragraphs[0]]
    removed = 0
    for para in paragraphs[1:]:
        pg = _trigrams(para)
        if not pg:
            kept.append(para)
            continue
        is_dup = False
        for prev in kept:
            prev_g = _trigrams(prev)
            if not prev_g:
                continue
            common = len(pg & prev_g)
            ratio = common / min(len(pg), len(prev_g))
            if ratio > threshold:
                is_dup = True
                break
        if is_dup:
            removed += 1
        else:
            kept.append(para)

    if removed:
        logger.info("Дедупликация абзацев: удалено %d повторных абзацев", removed)
    return '\n\n'.join(kept)


def _remove_fabricated_stats(text: str) -> str:
    """Заменить выдуманные проценты и статистику без указания автора.

    Если в предложении есть «XX%» но нет «Автор (Год)», проценты
    заменяются качественными формулировками.
    """
    def _fix_sentence(sent: str) -> str:
        if not re.search(r'\d{2,3}\s*%', sent):
            return sent
        # Есть процент — проверяем наличие атрибуции автора
        if re.search(r'[А-ЯЁA-Z][а-яёa-z]{2,}\s*\(\d{4}\)', sent):
            return sent  # есть автор+год — оставляем
        # Нет атрибуции — заменяем проценты
        sent = re.sub(r'на\s+\d{2,3}\s*%', 'существенно', sent)
        sent = re.sub(r'в\s+\d{2,3}\s*%\s*(случаев|ситуаций|классов|школ|групп)', r'в большинстве \1', sent)
        sent = re.sub(
            r'\d{2,3}\s*%\s*(учащихся|учеников|школьников|студентов|детей|обучающихся|педагогов|учителей|респондентов)',
            r'значительная часть \1', sent,
        )
        sent = re.sub(r'\d{2,3}\s*%', '', sent)
        sent = re.sub(r'  +', ' ', sent)
        return sent

    paragraphs = text.split('\n')
    result = []
    stats_removed = 0
    for para in paragraphs:
        sentences = re.split(r'(?<=[.!?])\s+', para)
        fixed = []
        for s in sentences:
            f = _fix_sentence(s)
            if f != s:
                stats_removed += 1
            fixed.append(f)
        result.append(' '.join(fixed))
    if stats_removed:
        logger.info("Удалено %d выдуманных статистик (проценты без автора)", stats_removed)
    return '\n'.join(result)


def clean_section_text(name: str, text: str) -> str:
    """Постобработка текста раздела: убрать markdown, дубль заголовка, AI-фразы."""
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

    # Удалить запрещённые AI-фразы (постпроцессинг — последний рубеж обороны)
    text = _remove_banned_phrases(text)

    # Удалить выдуманные проценты/статистику без указания автора
    text = _remove_fabricated_stats(text)

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
        "\n=== АБСОЛЮТНЫЕ ЗАПРЕТЫ (нарушение любого = переделка раздела) ==="
        "\nЗАПРЕЩЁННЫЕ ФРАЗЫ — НЕЛЬЗЯ использовать НИКОГДА, НИ В КАКОМ КОНТЕКСТЕ:"
        "\n«таким образом», «стоит отметить», «необходимо отметить», «следует подчеркнуть»,"
        "\n«следует отметить», «в заключение следует отметить», «не менее важным является»,"
        "\n«особое внимание следует уделить», «важно понимать, что», «нельзя не отметить»,"
        "\n«подводя итоги», «на основании вышеизложенного», «в данном контексте»,"
        "\n«представляется целесообразным», «в рамках данного исследования»,"
        "\n«в свою очередь», «в связи с этим», «кроме того, стоит подчеркнуть»."
        "\nЕсли ты обнаружишь, что написал любую из этих фраз — замени на другую конструкцию."
        "\n"
        "\nЗАПРЕТ НА БЕЗЫМЯННЫЕ ИССЛЕДОВАНИЯ:"
        "\n- НЕЛЬЗЯ писать «исследование 2020 года показало», «по данным исследований», «учёные доказали»."
        "\n- КАЖДАЯ ссылка на исследование ОБЯЗАНА содержать фамилию автора и год: «Иванов (2020) установил»."
        "\n"
        "\nЗАПРЕТ НА ВЫДУМАННЫЕ ПРОЦЕНТЫ И СТАТИСТИКУ:"
        "\n- НЕЛЬЗЯ писать «повышение на 20%», «в 85% случаев», «30% учеников», «снижение на 40%»."
        "\n- Точные проценты БЕЗ привязки к автору (Фамилия, Год) = фабрикация данных."
        "\n- Используй качественные оценки: «существенно», «в большинстве случаев», «заметное улучшение»."
        "\n- Конкретные цифры допустимы ТОЛЬКО со ссылкой: «Петров (2019) показал рост на 15%»."
        "\n"
        "\nЗАПРЕТ НА ОБОБЩАЮЩИЕ КОНЦОВКИ:"
        "\n- НЕ заканчивай абзац обобщающим предложением (это AI-маркер)."
        "\n- НЕ заканчивай раздел итоговым абзацем-выводом (кроме конца главы)."
        "\n- Последний абзац раздела должен содержать КОНКРЕТИКУ, а не резюме."
        "\n=== КОНЕЦ АБСОЛЮТНЫХ ЗАПРЕТОВ ==="
        "\n"
        "\nТРЕБОВАНИЯ К КАЧЕСТВУ:"
        "\n- Пиши академическим стилем без воды. Каждый абзац = конкретика: факт, цифра, пример, автор."
        "\n- НЕ повторяй одну мысль разными словами."
        "\n- Чередуй длинные (25-35 слов) и короткие (8-15 слов) предложения."
        "\n- Начинай абзацы по-разному: с факта, с имени автора, с вопроса, с примера. НЕ начинай"
        "\n  подряд два абзаца с одинаковой конструкции."
        "\n- Используй только общеизвестные, легко проверяемые данные."
        "\n- Используй не более 5-7 уникальных авторов (Фамилия, Год) на подраздел."
        "\n- Предпочитай ссылаться на авторов из предыдущих разделов, а не вводить новых."
        "\n- В КАЖДОМ подразделе допустимо 1-2 НОВЫХ автора, остальные — уже упомянутые ранее.",
        "\nВАЖНО:"
        "\n- НЕ начинай текст с заголовка раздела — заголовок добавляется автоматически."
        "\n- НЕ используй markdown-разметку (символы #, ##, **, *, >, ```, -)."
        "\n- Пиши только чистый текст абзацами, без форматирования.",
    ]

    # Специальные инструкции для библиографии
    if _is_bibliography(section):
        refs_hint = ""
        if cited_authors:
            refs_hint = (
                "\n\nКРИТИЧЕСКИ ВАЖНО — СОГЛАСОВАНИЕ С ТЕКСТОМ:"
                "\nВ тексте работы встречаются следующие ссылки (Автор + Год):"
                f"\n{', '.join(cited_authors)}"
                "\n"
                "\nДля КАЖДОЙ пары 'Автор (Год)' из списка выше ОБЯЗАТЕЛЬНО должен быть"
                "\nсоответствующий источник в библиографии С ТЕМ ЖЕ ГОДОМ ИЗДАНИЯ."
                "\nНапример: если в тексте 'Литвинов (2018)', то в библиографии должно быть"
                "\n'Литвинов, В. А. ... — 2018.' — именно 2018, а не 2008 или другой год."
                "\nНесовпадение года = грубая ошибка, которую преподаватель сразу заметит."
            )
        user_parts.append(
            "\nСТРОГИЕ ТРЕБОВАНИЯ К СПИСКУ ЛИТЕРАТУРЫ:"
            "\n- Оформление СТРОГО по ГОСТ Р 7.0.5-2008."
            "\n- Каждый источник — ОДНА СТРОКА. БЕЗ аннотаций, описаний и комментариев после выходных данных."
            "\n- Формат: Автор, И. О. Название работы / И. О. Автор. — Город: Издательство, Год. — Страниц с."
            "\n- Для зарубежных: Author, A. B. Title / A. B. Author. — City: Publisher, Year. — Pages p."
            "\n- Расположи в алфавитном порядке: сначала русскоязычные, затем иностранные."
            "\n- Каждая строка начинается с порядкового номера: 1. ..., 2. ..., и т.д."
            "\n"
            "\nОБЯЗАТЕЛЬНО — ИМЕНИТЕЛЬНЫЙ ПАДЕЖ:"
            "\n- Фамилии авторов в библиографии ТОЛЬКО в ИМЕНИТЕЛЬНОМ падеже."
            "\n- ПРАВИЛЬНО: Выготский, Л. С. / Томлинсон, К. / Петров, В. В."
            "\n- НЕПРАВИЛЬНО: Выготского, Л. С. / Томлинсона, К. / Петрова, В. В."
            "\n- Если автор упоминается в тексте как 'Виготского (1934)', в библиографии"
            "\n  пишем 'Выготский, Л. С.' — именительный падеж, правильная транслитерация."
            "\n"
            "\nЗАПРЕТ НА ФАБРИКАЦИЮ:"
            "\n- НЕ выдумывай книг с шаблонными названиями ('Педагогические инновации',"
            "\n  'Инновации в образовании', 'Современные методы обучения')."
            "\n- Используй РЕАЛЬНЫЕ, широко известные работы каждого автора."
            "\n- У каждого автора должна быть КОНКРЕТНАЯ, узнаваемая книга или статья по теме."
            "\n- Если не знаешь реальную работу автора — лучше пропусти, чем выдумывай."
            f"{refs_hint}"
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

    Стратегия: APPEND-режим — GPT генерирует ТОЛЬКО новые абзацы, которые
    дописываются к существующему тексту. Это гарантирует монотонный рост.
    Секции, которые перестали расти (<300 символов прироста), пропускаются.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0
    stalled: set[int] = set()  # индексы секций, которые перестали расти
    iters_done = 0

    for iteration in range(MAX_EXPAND_ITERATIONS):
        total_chars = sum(len(s.text) for s in sections)
        if total_chars >= target_chars:
            break

        needed_chars = target_chars - total_chars
        # Запрашиваем 300-600 слов за итерацию (1-2 страницы), не весь дефицит
        words_this_iter = min(600, max(300, needed_chars // 7))

        # Не расширяем: библиографию, заключение, введение и вводные абзацы глав
        expandable = [
            (i, s) for i, s in enumerate(sections)
            if i not in stalled
            and not _is_bibliography(SectionPlan(name=s.name, target_words=0))
            and not _is_conclusion(SectionPlan(name=s.name, target_words=0))
            and not _is_introduction(SectionPlan(name=s.name, target_words=0))
            and "глава" not in s.name.lower().split(".")[0].strip()
        ]
        if not expandable:
            # Все хорошие секции застопорились — попробуем заключение
            expandable = [
                (i, s) for i, s in enumerate(sections)
                if i not in stalled
                and not _is_bibliography(SectionPlan(name=s.name, target_words=0))
            ]
        if not expandable:
            logger.warning("Все секции застопорились, прерываем расширение на итерации %d", iteration + 1)
            break

        # Выбираем секцию: самая короткая среди expandable
        shortest_idx, shortest = min(expandable, key=lambda x: len(x[1].text))
        old_len = len(shortest.text)

        max_tokens = min(16000, max(2000, words_this_iter * 7))

        logger.info(
            "Расширение '%s' (итерация %d/%d): +%d слов, %d/%d символов, stalled=%d",
            shortest.name, iteration + 1, MAX_EXPAND_ITERATIONS,
            words_this_iter, total_chars, target_chars, len(stalled),
        )

        # Полный текст секции для контекста (лимит ~8000 символов для экономии токенов)
        existing_text = shortest.text
        if len(existing_text) > 8000:
            existing_text = (
                existing_text[:4000]
                + "\n[...середина раздела опущена...]\n"
                + existing_text[-4000:]
            )

        # Извлечь уже цитированных авторов (чтобы GPT не вводил новых)
        existing_authors = sorted(set(
            re.findall(r'([А-ЯЁA-Z][а-яёa-z]{2,})\s*\(\d{4}\)', existing_text)
        ))
        authors_hint = ", ".join(existing_authors[:20]) if existing_authors else "нет"

        # Извлечь темы последних абзацев (чтобы GPT не повторял)
        _paras = [p.strip() for p in existing_text.split('\n\n') if len(p.strip()) > 50]
        topics_hint = "\n".join(
            f"  - {p[:80].rstrip()}..." for p in _paras[-6:]
        ) if _paras else ""

        result = await chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"Тема работы: \"{title}\"\n"
                    f"Раздел: {shortest.name}\n\n"
                    f"ПОЛНЫЙ текст раздела (прочитай ВНИМАТЕЛЬНО, чтобы НЕ повторять):\n"
                    f"{existing_text}\n\n"
                    f"Напиши ДОПОЛНИТЕЛЬНЫЕ абзацы для этого раздела — "
                    f"примерно {words_this_iter} слов нового текста. "
                    f"Эти абзацы будут ДОБАВЛЕНЫ в конец раздела."
                    f"\n\nАвторы, уже цитированные в тексте: {authors_hint}."
                    f"\nИспользуй ТОЛЬКО этих авторов для ссылок. НЕ вводи новых фамилий."
                    + (f"\n\nТемы последних абзацев (НЕ повторять эти темы):\n{topics_hint}" if topics_hint else "")
                    + f"\n\nКРИТИЧЕСКИЕ ТРЕБОВАНИЯ:"
                    f"\n- Прочитай существующий текст ЦЕЛИКОМ."
                    f"\n- Определи ВСЕ темы и аргументы, которые УЖЕ раскрыты."
                    f"\n- Пиши ИСКЛЮЧИТЕЛЬНО о том, что ещё НЕ упомянуто."
                    f"\n- НЕ вводи НОВЫХ авторов — ссылайся ТОЛЬКО на уже цитированных."
                    f"\n- Рассматривай НОВЫЕ аспекты темы, не затронутые ранее."
                    f"\n- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО перефразировать или повторять уже написанное."
                    f"\n- Текст должен ЛОГИЧЕСКИ ПРОДОЛЖАТЬ существующий раздел."
                    f"\n"
                    f"\nФОРМАТ:"
                    f"\n- Верни ТОЛЬКО новые абзацы (НЕ повторяй существующий текст)."
                    f"\n- НЕ начинай с заголовка раздела."
                    f"\n- НЕ используй markdown-разметку."
                    f"\n- Пиши только чистый текст абзацами."
                    f"\n- НЕ заканчивай абзацы обобщающими предложениями."
                    f"\n- НЕ используй выдуманные проценты и статистику без автора (Год)."
                    f"\n- ЗАПРЕЩЁННЫЕ ФРАЗЫ: «таким образом», «стоит отметить»,"
                    f"\n  «необходимо отметить», «следует подчеркнуть», «в данном контексте»."
                    f"\n- Пиши минимум {words_this_iter} слов."
                )},
            ],
            model=settings.openai_model_main,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        new_text = clean_section_text(shortest.name, result["content"])
        # APPEND: дописываем к существующему тексту
        combined = shortest.text.rstrip() + "\n\n" + new_text.lstrip()
        # Дедупликация: убрать абзацы, повторяющие ранее написанное
        combined = _dedup_paragraphs(combined)

        sections[shortest_idx] = GeneratedSection(
            name=shortest.name,
            text=combined,
            target_words=shortest.target_words,
        )
        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_cost += result["cost_usd"]

        iters_done += 1
        growth = len(combined) - old_len
        logger.info("  Прирост: +%d символов (+%.1f стр.)", growth, growth / CHARS_PER_PAGE)
        if growth < 300:
            logger.warning("  Секция '%s' застопорилась (прирост %d < 300)", shortest.name, growth)
            stalled.add(shortest_idx)

    total_chars = sum(len(s.text) for s in sections)
    logger.info(
        "Расширение завершено: %d/%d символов (~%.0f стр.), итераций: %d",
        total_chars, target_chars, total_chars / CHARS_PER_PAGE, iters_done,
    )

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


def _normalize_russian_surname(surname: str) -> str:
    """Нормализовать русскую фамилию: убрать женские окончания, привести к основе.

    Петрова → петров, Смирнова → смирнов, Лебедева → лебедев, Кузнецова → кузнецов
    """
    s = surname.lower()
    # Убираем женские окончания: -ова → -ов, -ева → -ев, -ина → -ин, -ая → -ый/ой
    for fem, masc in [('ова', 'ов'), ('ева', 'ев'), ('ёва', 'ёв'), ('ина', 'ин'), ('ская', 'ск'), ('цкая', 'цк')]:
        if s.endswith(fem):
            s = s[:-len(fem)] + masc
            break
    return s


def _bib_dedup_key(entry_text: str) -> tuple[str, str, str]:
    """Извлечь фамилию автора, первый инициал и год из библиографической записи.

    Возвращает (normalized_surname, initial_lower, year).
    Фамилия нормализована: Петрова → петров, Смирнова → смирнов.
    """
    # Русский автор: "Фамилия, И. О." или "Фамилия И.О."
    m = re.match(r'([А-ЯЁ][а-яё]+)\s*,?\s*([А-ЯЁ])\s*\.', entry_text)
    if m:
        surname = _normalize_russian_surname(m.group(1))
        initial = m.group(2).lower()
    else:
        # Иностранный: "Surname, A. B." или "Surname A."
        m = re.match(r'([A-Z][a-z]+)\s*,?\s*([A-Z])\s*\.', entry_text)
        if m:
            surname = m.group(1).lower()
            initial = m.group(2).lower()
        else:
            # Фолбэк: первое слово
            surname = entry_text.split()[0].lower().rstrip(',.:') if entry_text.split() else ""
            initial = ""

    year_match = re.search(r'(\d{4})', entry_text)
    year = year_match.group(1) if year_match else ""

    return surname, initial, year


def _clean_bibliography(text: str, max_entries: int = 25) -> str:
    """Постпроцессинг библиографии: агрессивная дедупликация, сортировка, перенумерация.

    Дедупликация:
    - Фамилия (нормализованная) + инициал + год = дубль (удаляется)
    - Максимум 2 записи на одну фамилию (даже с разными годами/инициалами)
    - Максимум max_entries записей (по умолчанию 25)
    """
    MAX_PER_SURNAME = 2

    entries = re.split(r'\n(?=\d+\.)', text.strip())
    cleaned = []
    seen_exact: set[str] = set()       # "surname_initial_year"
    surname_count: dict[str, int] = {}  # surname -> count

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        without_num = re.sub(r'^\d+\.\s*', '', entry).strip()
        if not without_num:
            continue

        surname, initial, year = _bib_dedup_key(without_num)
        exact_key = f"{surname}_{initial}_{year}"

        # Точный дубль: та же фамилия + инициал + год
        if exact_key in seen_exact:
            logger.debug("Библиография: дубль '%s' (ключ %s)", without_num[:50], exact_key)
            continue
        seen_exact.add(exact_key)

        # Лимит записей на одну фамилию
        count = surname_count.get(surname, 0)
        if count >= MAX_PER_SURNAME:
            logger.debug("Библиография: >%d записей '%s', пропуск", MAX_PER_SURNAME, surname)
            continue
        surname_count[surname] = count + 1

        cleaned.append(without_num)

    # Сортировка: русские → иностранные, алфавитно
    russian = [e for e in cleaned if re.match(r'[А-ЯЁа-яё]', e)]
    foreign = [e for e in cleaned if not re.match(r'[А-ЯЁа-яё]', e)]
    russian.sort(key=str.lower)
    foreign.sort(key=str.lower)

    all_sorted = russian + foreign

    # Лимит общего количества записей
    if len(all_sorted) > max_entries:
        logger.info("Библиография: обрезка %d → %d записей", len(all_sorted), max_entries)
        all_sorted = all_sorted[:max_entries]

    # Перенумеровать
    final = []
    for i, entry in enumerate(all_sorted, 1):
        final.append(f"{i}. {entry}")

    logger.info("Библиография после очистки: %d записей (из %d)", len(final), len(entries))
    return "\n".join(final)


def _find_missing_in_bibliography(bib_text: str, cited_authors: list[str]) -> list[str]:
    """Найти цитируемых авторов, которые отсутствуют в тексте библиографии.

    Сопоставление нечёткое: ищем фамилию (без падежных окончаний) и год.
    """
    bib_lower = bib_text.lower()
    missing = []
    for ref in cited_authors:
        # ref = "Выготский (1934)" или "Tomlinson (2001)"
        m = re.match(r'(.+?)\s*\((\d{4})\)', ref)
        if not m:
            continue
        surname = m.group(1).strip()
        year = m.group(2)

        # Для русских: берём основу фамилии (без окончания -а, -ой, -ого и т.д.)
        if re.match(r'[А-ЯЁ]', surname):
            stem = re.sub(r'(ой|ого|ину|ина|ова|ева|ёва|ый|ий|а)$', '', surname.lower())
            # Минимум 3 символа основы
            if len(stem) < 3:
                stem = surname.lower()[:4]
        else:
            stem = surname.lower()

        # Ищем основу фамилии + год в тексте библиографии
        if stem in bib_lower and year in bib_text:
            # Проверяем что год рядом с фамилией (в пределах одной записи ~500 символов)
            for pos in [m.start() for m in re.finditer(re.escape(stem), bib_lower)]:
                chunk = bib_text[max(0, pos - 50):pos + 300]
                if year in chunk:
                    break
            else:
                missing.append(ref)
        else:
            missing.append(ref)
    return missing


async def _validate_bibliography(
    bib_section: GeneratedSection,
    cited_authors: list[str],
    title: str,
    subject: str,
    system_prompt: str,
    max_entries: int = 25,
) -> tuple[GeneratedSection, dict]:
    """Проверить библиографию на пропущенных авторов и дополнить при необходимости.

    Возвращает (обновлённую секцию, токены). Если пропусков нет — возвращает оригинал.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0

    # Подсчитаем текущие записи
    current_count = len(re.findall(r'^\d+\.', bib_section.text, re.MULTILINE))

    for attempt in range(2):  # Максимум 2 попытки дополнения
        missing = _find_missing_in_bibliography(bib_section.text, cited_authors)
        if not missing:
            logger.info("Библиография: все цитируемые авторы найдены")
            break

        # Не добавляем, если уже достаточно записей
        if current_count >= max_entries:
            logger.info("Библиография: уже %d записей (cap=%d), пропускаем добавление %d авторов",
                         current_count, max_entries, len(missing))
            break

        # Ограничиваем количество добавляемых за раз (не более 10)
        missing = missing[:10]

        logger.warning(
            "Библиография: %d авторов не найдены (попытка %d): %s",
            len(missing), attempt + 1, ", ".join(missing[:10]),
        )

        # Дополнительный API-вызов: добавить недостающие записи
        result = await chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"Тема работы: \"{title}\"\nПредмет: {subject}\n\n"
                    f"В тексте работы цитируются следующие авторы, но их НЕТ в списке литературы:\n"
                    + "\n".join(f"- {ref}" for ref in missing)
                    + "\n\nДопиши ТОЛЬКО недостающие библиографические записи для этих авторов."
                    "\nФормат СТРОГО по ГОСТ Р 7.0.5-2008."
                    "\nФамилия автора ОБЯЗАТЕЛЬНО в ИМЕНИТЕЛЬНОМ падеже (Выготский, а не Выготского)."
                    "\nГод издания ОБЯЗАТЕЛЬНО должен совпадать с годом в скобках."
                    "\nНапример: для 'Хатти (2009)' нужно: Хатти, Дж. Видимое обучение / Дж. Хатти. — М.: Национальное образование, 2009. — 496 с."
                    "\nИспользуй РЕАЛЬНЫЕ, широко известные работы этих авторов."
                    "\nНумерацию продолжай с номера, следующего за последним в текущем списке."
                    "\nВерни ТОЛЬКО новые записи, без существующих."
                )},
            ],
            model=settings.openai_model_main,
            temperature=0.3,
            max_tokens=2000,
        )
        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_cost += result["cost_usd"]

        extra_entries = result["content"].strip()
        if extra_entries:
            # Добавляем новые записи к библиографии
            combined = bib_section.text.rstrip() + "\n\n" + extra_entries
            # Дедупликация + перенумерация
            combined = _clean_bibliography(combined, max_entries=max_entries)
            bib_section = GeneratedSection(
                name=bib_section.name,
                text=combined,
                target_words=bib_section.target_words,
            )

    # Финальная очистка библиографии (дедупликация, сортировка, перенумерация)
    bib_section = GeneratedSection(
        name=bib_section.name,
        text=_clean_bibliography(bib_section.text, max_entries=max_entries),
        target_words=bib_section.target_words,
    )

    tokens_info = {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": total_cost,
    }
    return bib_section, tokens_info


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
        if _is_introduction(section):
            extra = (
                "\nТРЕБОВАНИЯ К ВВЕДЕНИЮ (строго следуй этой структуре):"
                "\nВведение — это НЕ пересказ содержания работы и НЕ автореферат."
                "\nСтруктура введения (каждый пункт — 1-2 абзаца):"
                "\n1. Актуальность темы (почему эта тема важна СЕЙЧАС, со ссылками на авторов)"
                "\n2. Степень разработанности проблемы (кратко: кто из учёных занимался этой темой)"
                "\n3. Цель исследования (ОДНО предложение)"
                "\n4. Задачи исследования (3-4 конкретные задачи)"
                "\n5. Объект и предмет исследования (по 1 предложению)"
                "\n6. Методы исследования: перечисляй ТОЛЬКО теоретические методы — "
                "анализ научной литературы, систематизация и обобщение научных данных, "
                "сравнительный анализ, классификация, метод теоретического моделирования. "
                "НЕ упоминай эмпирические методы (анкетирование, наблюдение, эксперимент, "
                "метод экспертных оценок), если в работе нет собственного эмпирического исследования"
                "\n7. Структура работы (1-2 предложения: 'Работа состоит из введения, N глав, "
                "заключения и списка литературы')"
                "\n"
                "\nАБСОЛЮТНЫЕ ЗАПРЕТЫ ДЛЯ ВВЕДЕНИЯ:"
                "\n- НЕ раскрывай содержание глав подробно!"
                "\n- НЕ пересказывай теорию, результаты анализа или практические рекомендации!"
                "\n- НЕ приводи конкретные примеры, методики, классификации из основной части!"
                "\n- Введение = ТОЛЬКО постановка проблемы + методологический аппарат."
                "\nОбъём: 2-3 страницы (500-750 слов)."
            )
        elif _is_chapter_intro(section, content_plan, idx):
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

    # Шаг 2.5: Генерация библиографии (если есть) с учётом цитируемых авторов и годов
    if bib_plan:
        cited_authors = _extract_cited_references(sw.sections)
        # Динамический cap: min 25, max 40, на основе числа цитируемых авторов
        max_bib = max(25, min(40, len(cited_authors) + 5))
        logger.info("Найдено %d цитируемых авторов для библиографии (cap=%d): %s",
                     len(cited_authors), max_bib, ", ".join(cited_authors[:10]))

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

            # Валидация: проверяем, все ли цитируемые авторы попали в библиографию
            if cited_authors:
                generated, extra_tokens = await _validate_bibliography(
                    bib_section=generated,
                    cited_authors=cited_authors,
                    title=title,
                    subject=subject,
                    system_prompt=system_prompt,
                    max_entries=max_bib,
                )
                # Заменяем секцию на исправленную
                sw.sections[-1] = generated
                _accumulate(sw, extra_tokens)

    # Шаг 3: Расширение до целевого объёма
    sw.sections, tokens_info = await expand_to_target(
        sections=sw.sections,
        target_chars=target_chars,
        title=title,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    _accumulate(sw, tokens_info)

    # Шаг 4: Повторная валидация библиографии после расширения
    # expand_to_target добавляет текст с новыми цитатами — нужно проверить их
    bib_idx = next(
        (i for i, s in enumerate(sw.sections)
         if _is_bibliography(SectionPlan(name=s.name, target_words=0))),
        None,
    )
    if bib_idx is not None:
        post_expand_refs = _extract_cited_references(sw.sections)
        # Пересчитать cap после расширения
        max_bib = max(25, min(40, len(post_expand_refs) + 5))
        if post_expand_refs:
            logger.info(
                "Пост-расширение: %d цитируемых авторов (cap=%d), проверяем библиографию",
                len(post_expand_refs), max_bib,
            )
            updated_bib, extra_tokens = await _validate_bibliography(
                bib_section=sw.sections[bib_idx],
                cited_authors=post_expand_refs,
                title=title,
                subject=subject,
                system_prompt=system_prompt,
                max_entries=max_bib,
            )
            sw.sections[bib_idx] = updated_bib
            _accumulate(sw, extra_tokens)

    # Собираем финальный текст
    sw.text = assemble_text(sw.sections, uppercase_names=uppercase_headings)

    return sw
