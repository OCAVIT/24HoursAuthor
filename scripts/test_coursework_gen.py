"""Тестовая генерация курсовой работы с реальным API и проверка результата."""

import asyncio
import logging
import re
import sys
import os
import time

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.generator.coursework import generate
from src.docgen.builder import build_docx, _sections_from_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_gen")

# Параметры заказа
TITLE = "Использование дифференцированного подхода в обучении младших школьников"
SUBJECT = "Педагогика"
PAGES = 40
REQUIRED_UNIQUENESS = 80

# Запрещённые AI-фразы (из промпта)
BANNED_PHRASES = [
    "таким образом",
    "необходимо отметить",
    "следует подчеркнуть",
    "стоит отметить",
    "в заключение следует отметить",
    "не менее важным является",
    "особое внимание следует уделить",
    "в данном контексте",
    "важно понимать, что",
]


def check_text_quality(text: str) -> dict:
    """Проверить качество текста по ключевым критериям."""
    results = {}
    text_lower = text.lower()

    # 1. Длина
    chars = len(text)
    words = len(text.split())
    pages_approx = chars // 1800
    results["chars"] = chars
    results["words"] = words
    results["pages"] = pages_approx

    # 2. Запрещённые фразы
    banned_found = {}
    for phrase in BANNED_PHRASES:
        count = text_lower.count(phrase)
        if count > 0:
            banned_found[phrase] = count
    results["banned_phrases"] = banned_found
    results["total_banned"] = sum(banned_found.values())

    # 3. Структура: наличие ключевых разделов
    has_intro = "ВВЕДЕНИЕ" in text or "Введение" in text
    has_conclusion = "ЗАКЛЮЧЕНИЕ" in text or "Заключение" in text
    has_bib = "СПИСОК ЛИТЕРАТУРЫ" in text or "Список литературы" in text
    results["has_intro"] = has_intro
    results["has_conclusion"] = has_conclusion
    results["has_bibliography"] = has_bib

    # 4. Подразделы
    subsection_pattern = re.compile(r'^\d+\.\d+\.?\s+[A-ZА-ЯЁ]', re.MULTILINE)
    subsections = subsection_pattern.findall(text)
    results["subsection_count"] = len(subsections)

    # 5. Главы
    chapter_pattern = re.compile(r'^(Глава\s+\d+|ГЛАВА\s+\d+)', re.MULTILINE)
    chapters = chapter_pattern.findall(text)
    results["chapter_count"] = len(chapters)

    # 6. Библиография: количество источников
    bib_start = text.find("СПИСОК ЛИТЕРАТУРЫ")
    if bib_start < 0:
        bib_start = text.find("Список литературы")
    if bib_start >= 0:
        bib_text = text[bib_start:]
        bib_entries = re.findall(r'^\d+\.', bib_text, re.MULTILINE)
        results["bib_entries"] = len(bib_entries)

        # Проверяем аннотации (длинные строки после библиографических записей)
        bib_lines = [l.strip() for l in bib_text.split("\n") if l.strip()]
        annotation_count = 0
        for line in bib_lines[1:]:  # пропускаем заголовок
            if line and not re.match(r'^\d+\.', line) and len(line) > 20:
                annotation_count += 1
        results["bib_annotations"] = annotation_count
    else:
        results["bib_entries"] = 0
        results["bib_annotations"] = 0

    # 7. Цитируемые авторы vs библиография
    # Извлекаем авторов из текста (до библиографии)
    content_text = text[:bib_start] if bib_start > 0 else text
    cited_foreign = set(m.group(1) for m in re.finditer(r'([A-Z][a-z]{2,})\s*\(\d{4}\)', content_text))
    cited_russian = set(m.group(1) for m in re.finditer(r'([А-ЯЁ][а-яё]{2,})\s*\(\d{4}\)', content_text))
    results["cited_foreign"] = sorted(cited_foreign)
    results["cited_russian"] = sorted(cited_russian)

    # Проверяем, есть ли они в библиографии
    if bib_start >= 0:
        bib_text_lower = text[bib_start:].lower()
        missing_foreign = [a for a in cited_foreign if a.lower() not in bib_text_lower]
        missing_russian = [a for a in cited_russian if a.lower() not in bib_text_lower]
        results["missing_in_bib_foreign"] = missing_foreign
        results["missing_in_bib_russian"] = missing_russian
    else:
        results["missing_in_bib_foreign"] = list(cited_foreign)
        results["missing_in_bib_russian"] = list(cited_russian)

    # 8. Заключение: длина
    conc_start = text.find("ЗАКЛЮЧЕНИЕ")
    if conc_start < 0:
        conc_start = text.find("Заключение")
    if conc_start >= 0 and bib_start > conc_start:
        conclusion_text = text[conc_start:bib_start]
        results["conclusion_words"] = len(conclusion_text.split())
        results["conclusion_pages"] = len(conclusion_text) // 1800
    elif conc_start >= 0:
        conclusion_text = text[conc_start:]
        results["conclusion_words"] = len(conclusion_text.split())
        results["conclusion_pages"] = len(conclusion_text) // 1800

    # 9. Markdown артефакты
    md_artifacts = len(re.findall(r'[#*>`]', text))
    results["markdown_artifacts"] = md_artifacts

    return results


def check_sections_parsing(text: str) -> dict:
    """Проверить парсинг секций builder.py."""
    sections = _sections_from_text(text)

    info = {
        "total_sections": len(sections),
        "level1": [],
        "level2": [],
    }
    for s in sections:
        entry = f"{s['heading'][:60]}..." if len(s.get('heading', '')) > 60 else s.get('heading', '')
        body_len = len(s.get('text', ''))
        if s.get('level', 1) == 1:
            info["level1"].append(f"[L1] {entry} ({body_len} chars)")
        else:
            info["level2"].append(f"[L2] {entry} ({body_len} chars)")

    return info


async def main():
    start = time.time()

    logger.info("=" * 70)
    logger.info("ТЕСТОВАЯ ГЕНЕРАЦИЯ КУРСОВОЙ РАБОТЫ")
    logger.info("Тема: %s", TITLE)
    logger.info("Предмет: %s", SUBJECT)
    logger.info("Страниц: %d, Уникальность: %d%%", PAGES, REQUIRED_UNIQUENESS)
    logger.info("=" * 70)

    # === 1. Генерация текста ===
    logger.info("\n[1/3] Генерация текста через API...")
    result = await generate(
        title=TITLE,
        description="Курсовая работа по педагогике на тему дифференцированного подхода в обучении младших школьников.",
        subject=SUBJECT,
        pages=PAGES,
        required_uniqueness=REQUIRED_UNIQUENESS,
    )

    gen_time = time.time() - start
    logger.info("Генерация завершена за %.1f сек", gen_time)
    logger.info("Токены: %d (in: %d, out: %d), Стоимость: $%.4f",
                result.total_tokens, result.input_tokens, result.output_tokens, result.cost_usd)

    # Сохраняем текст
    text_path = os.path.join(os.path.dirname(__file__), "test_gen_output.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(result.text)
    logger.info("Текст сохранён: %s", text_path)

    # === 2. Проверка качества текста ===
    logger.info("\n[2/3] ПРОВЕРКА КАЧЕСТВА ТЕКСТА")
    logger.info("-" * 50)

    quality = check_text_quality(result.text)

    # Объём
    logger.info("Объём: %d символов, %d слов, ~%d страниц (цель: %d)",
                quality["chars"], quality["words"], quality["pages"], PAGES)

    # Структура
    logger.info("Структура:")
    logger.info("  Введение: %s", "OK" if quality["has_intro"] else "ОТСУТСТВУЕТ!")
    logger.info("  Глав: %d", quality["chapter_count"])
    logger.info("  Подразделов: %d", quality["subsection_count"])
    logger.info("  Заключение: %s", "OK" if quality["has_conclusion"] else "ОТСУТСТВУЕТ!")
    logger.info("  Библиография: %s", "OK" if quality["has_bibliography"] else "ОТСУТСТВУЕТ!")

    # Заключение
    if "conclusion_words" in quality:
        logger.info("  Заключение: %d слов (~%d стр.)", quality["conclusion_words"], quality["conclusion_pages"])
        if quality["conclusion_words"] > 900:
            logger.warning("  ⚠ Заключение слишком длинное (>900 слов)!")
        else:
            logger.info("  ✓ Заключение нормальной длины")

    # Библиография
    logger.info("Библиография: %d записей, %d аннотаций", quality["bib_entries"], quality["bib_annotations"])
    if quality["bib_annotations"] > 2:
        logger.warning("  ⚠ В библиографии обнаружены аннотации после записей!")
    else:
        logger.info("  ✓ Библиография без аннотаций")

    # Цитируемые авторы
    logger.info("Цитируемые авторы (русские): %s", ", ".join(quality["cited_russian"][:10]))
    logger.info("Цитируемые авторы (зарубежные): %s", ", ".join(quality["cited_foreign"][:10]))
    if quality["missing_in_bib_foreign"]:
        logger.warning("  ⚠ Зарубежные авторы НЕ в библиографии: %s", ", ".join(quality["missing_in_bib_foreign"]))
    else:
        logger.info("  ✓ Все зарубежные авторы в библиографии")
    if quality["missing_in_bib_russian"]:
        logger.warning("  ⚠ Русские авторы НЕ в библиографии: %s", ", ".join(quality["missing_in_bib_russian"][:5]))
    else:
        logger.info("  ✓ Все русские авторы в библиографии")

    # Запрещённые фразы
    if quality["total_banned"] > 0:
        logger.warning("Запрещённые AI-фразы: %d штук", quality["total_banned"])
        for phrase, count in quality["banned_phrases"].items():
            logger.warning("  '%s': %d раз", phrase, count)
    else:
        logger.info("✓ Запрещённые AI-фразы не обнаружены")

    # Markdown
    if quality["markdown_artifacts"] > 10:
        logger.warning("⚠ Markdown-артефакты: %d символов (#, *, >, `)", quality["markdown_artifacts"])
    else:
        logger.info("✓ Markdown-артефакты: %d (допустимо)", quality["markdown_artifacts"])

    # === 2.5. Проверка парсинга секций ===
    logger.info("\n[2.5] ПАРСИНГ СЕКЦИЙ (builder.py)")
    logger.info("-" * 50)
    sections_info = check_sections_parsing(result.text)
    logger.info("Всего секций: %d", sections_info["total_sections"])
    logger.info("Level 1 (главы):")
    for s in sections_info["level1"]:
        logger.info("  %s", s)
    logger.info("Level 2 (подразделы):")
    for s in sections_info["level2"]:
        logger.info("  %s", s)

    # Проверяем: библиография не разбита на отдельные секции
    bib_sections = [s for s in sections_info["level1"] + sections_info["level2"] if "литератур" in s.lower() or "библиограф" in s.lower()]
    if len(bib_sections) > 1:
        logger.warning("⚠ Библиография разбита на %d секций (должна быть 1)!", len(bib_sections))
    elif len(bib_sections) == 1:
        logger.info("✓ Библиография — одна секция")

    # === 3. Генерация DOCX ===
    logger.info("\n[3/3] ГЕНЕРАЦИЯ DOCX")
    logger.info("-" * 50)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "tmp", "test_gen")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "test_coursework.docx")

    docx_path = await build_docx(
        title=TITLE,
        text=result.text,
        work_type="Курсовая работа",
        subject=SUBJECT,
        output_path=output_file,
    )

    if docx_path:
        file_size = os.path.getsize(docx_path)
        logger.info("✓ DOCX создан: %s (%d KB)", docx_path, file_size // 1024)
    else:
        logger.error("✗ Не удалось создать DOCX!")

    # === Итог ===
    total_time = time.time() - start
    logger.info("\n" + "=" * 70)
    logger.info("ИТОГ")
    logger.info("=" * 70)
    logger.info("Время: %.1f сек (%.1f мин)", total_time, total_time / 60)
    logger.info("Стоимость API: $%.4f (~%.0f руб.)", result.cost_usd, result.cost_usd * 90)
    logger.info("Объём: ~%d стр. (цель %d)", quality["pages"], PAGES)

    # Оценка
    issues = []
    if quality["pages"] < PAGES * 0.7:
        issues.append(f"Объём {quality['pages']} стр. < 70% от цели ({PAGES})")
    if not quality["has_intro"]:
        issues.append("Нет введения")
    if not quality["has_conclusion"]:
        issues.append("Нет заключения")
    if not quality["has_bibliography"]:
        issues.append("Нет библиографии")
    if quality["total_banned"] > 5:
        issues.append(f"Много AI-фраз: {quality['total_banned']}")
    if quality["bib_annotations"] > 2:
        issues.append(f"Аннотации в библиографии: {quality['bib_annotations']}")
    if quality["missing_in_bib_foreign"]:
        issues.append(f"Авторы не в библиографии: {', '.join(quality['missing_in_bib_foreign'][:3])}")
    if quality.get("conclusion_words", 0) > 900:
        issues.append(f"Заключение {quality['conclusion_words']} слов (норма < 900)")
    if quality["subsection_count"] < 4:
        issues.append(f"Мало подразделов: {quality['subsection_count']}")

    if issues:
        logger.warning("\nПРОБЛЕМЫ (%d):", len(issues))
        for i, issue in enumerate(issues, 1):
            logger.warning("  %d. %s", i, issue)
    else:
        logger.info("\n✓ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


if __name__ == "__main__":
    asyncio.run(main())
