"""Сборка DOCX файлов через Node.js subprocess (docx-js)."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "generate_docx.js"


async def build_docx(
    title: str,
    text: str,
    work_type: str = "Реферат",
    subject: str = "",
    author: str = "",
    university: str = "",
    font_size: int = 14,
    line_spacing: float = 1.5,
    output_path: Optional[str] = None,
    plan: Optional[dict] = None,
) -> Optional[Path]:
    """Сгенерировать DOCX файл из текста через Node.js.

    Args:
        title: Заголовок работы.
        text: Полный текст работы.
        work_type: Тип работы (Эссе, Реферат, Курсовая и т.д.).
        subject: Предмет.
        author: Автор (если известен).
        university: Вуз (если известен).
        font_size: Размер шрифта.
        line_spacing: Межстрочный интервал.
        output_path: Путь для сохранения (если не указан — автогенерация).
        plan: План работы (для структурирования секций).

    Returns:
        Path к сгенерированному файлу или None при ошибке.
    """
    sections = _parse_text_to_sections(text, plan)

    data = {
        "title": title,
        "work_type": work_type,
        "subject": subject,
        "author": author,
        "university": university,
        "sections": sections,
        "font_size": font_size,
        "line_spacing": line_spacing,
        "output_path": output_path or "",
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(SCRIPT_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        input_json = json.dumps(data, ensure_ascii=False).encode("utf-8")
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=input_json),
            timeout=60,
        )

        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            logger.error("Ошибка генерации DOCX: %s", error)
            return None

        file_path = stdout.decode("utf-8").strip()
        if not file_path:
            logger.error("Node.js вернул пустой путь")
            return None

        result_path = Path(file_path)
        if not result_path.exists():
            logger.error("Файл не найден: %s", file_path)
            return None

        logger.info("DOCX сгенерирован: %s (%d bytes)", file_path, result_path.stat().st_size)
        return result_path

    except asyncio.TimeoutError:
        logger.error("Таймаут генерации DOCX (60 сек)")
        return None
    except FileNotFoundError:
        logger.error("Node.js не найден. Установите Node.js для генерации DOCX.")
        return None
    except Exception as e:
        logger.error("Неожиданная ошибка при генерации DOCX: %s", e)
        return None


def _parse_text_to_sections(text: str, plan: Optional[dict] = None) -> list[dict]:
    """Разобрать текст на секции по заголовкам.

    Если план передан — используем структуру из плана.
    Иначе — пытаемся найти заголовки по паттернам.
    """
    if plan and plan.get("chapters"):
        return _sections_from_plan(text, plan)

    return _sections_from_text(text)


def _sections_from_plan(text: str, plan: dict) -> list[dict]:
    """Разбить текст на секции по плану."""
    sections = []
    remaining = text

    # Ищем маркеры введения, глав, заключения, списка литературы
    markers = ["ВВЕДЕНИЕ", "Введение"]
    for ch in plan.get("chapters", []):
        ch_title = ch.get("title", "")
        markers.append(ch_title)
        markers.append(ch_title.upper())
        if ch.get("number"):
            markers.append(f"Глава {ch['number']}")
            markers.append(f"ГЛАВА {ch['number']}")
    markers.extend(["ЗАКЛЮЧЕНИЕ", "Заключение", "СПИСОК ЛИТЕРАТУРЫ", "Список литературы"])

    # Разбиваем текст по маркерам
    parts = _split_by_markers(remaining, markers)

    for heading, body in parts:
        level = _detect_heading_level(heading)

        sections.append({
            "heading": heading,
            "text": body.strip(),
            "level": level,
        })

    if not sections:
        sections.append({"heading": "", "text": text, "level": 1})

    return sections


def _detect_heading_level(heading: str) -> int:
    """Определить уровень заголовка: 1 = глава/введение/заключение, 2 = подраздел."""
    if not heading:
        return 1
    h = heading.strip()
    # Подразделы: "1.1 ...", "2.3 ...", "1.1. ..." — цифра.цифра
    if re.match(r'^\d+\.\d+', h):
        return 2
    return 1


def _is_bibliography_heading(heading: str) -> bool:
    """Проверить, является ли заголовок разделом библиографии."""
    h = heading.strip().lower()
    return "список литературы" in h or "библиограф" in h or "список источников" in h


def _sections_from_text(text: str) -> list[dict]:
    """Разбить текст на секции по обнаруженным заголовкам.

    Ключевые улучшения:
    - Подразделы (1.1, 2.3) корректно определяются как level 2
    - После "СПИСОК ЛИТЕРАТУРЫ" все строки — тело библиографии, не заголовки
    - Нумерованные строки библиографии не разбиваются на отдельные секции
    """
    # Паттерны заголовков
    heading_patterns = [
        r"^(ВВЕДЕНИЕ|ЗАКЛЮЧЕНИЕ|СПИСОК ЛИТЕРАТУРЫ|СОДЕРЖАНИЕ)$",
        r"^(Введение|Заключение|Список литературы|Содержание)$",
        r"^(Глава\s+\d+[.\s]*.*)$",
        r"^(ГЛАВА\s+\d+[.\s]*.*)$",
        # Подразделы: 1.1 Текст, 2.3 Текст, 1.1. Текст (цифра.цифра[.] пробел текст)
        r"^(\d+\.\d+\.?\s+[A-ZА-ЯЁ].*)$",
        # Главы без слова "Глава": 1. Текст, 2. Текст (одиночная цифра.пробел)
        # НО: не должно быть внутри библиографии (обрабатывается через state)
        r"^(\d+\.\s+[A-ZА-ЯЁ][A-ZА-ЯЁa-zа-яё].*)$",
    ]
    combined_pattern = "|".join(heading_patterns)

    lines = text.split("\n")
    sections = []
    current_heading = ""
    current_body_lines: list[str] = []
    in_bibliography = False  # Флаг: мы внутри раздела библиографии

    for line in lines:
        stripped = line.strip()

        # Если мы внутри библиографии — все строки идут как тело
        if in_bibliography:
            current_body_lines.append(line)
            continue

        if stripped and re.match(combined_pattern, stripped, re.MULTILINE):
            # Сохраняем предыдущую секцию
            if current_heading or current_body_lines:
                sections.append({
                    "heading": current_heading,
                    "text": "\n".join(current_body_lines).strip(),
                    "level": _detect_heading_level(current_heading),
                })
            current_heading = stripped
            current_body_lines = []

            # Проверяем: это начало библиографии?
            if _is_bibliography_heading(stripped):
                in_bibliography = True
        else:
            current_body_lines.append(line)

    # Последняя секция
    if current_heading or current_body_lines:
        sections.append({
            "heading": current_heading,
            "text": "\n".join(current_body_lines).strip(),
            "level": _detect_heading_level(current_heading),
        })

    if not sections:
        sections.append({"heading": "", "text": text, "level": 1})

    return sections


def _split_by_markers(text: str, markers: list[str]) -> list[tuple[str, str]]:
    """Разбить текст по маркерам-заголовкам."""
    parts = []

    found_positions: list[tuple[int, str]] = []
    text_lower = text.lower()
    for marker in markers:
        pos = text_lower.find(marker.lower())
        if pos >= 0:
            found_positions.append((pos, marker))

    # Сортируем по позиции и убираем дубликаты (близкие позиции)
    found_positions.sort(key=lambda x: x[0])
    # Дедупликация: если два маркера в пределах 5 символов — оставляем первый
    deduped = []
    for pos, marker in found_positions:
        if not deduped or pos - deduped[-1][0] > 5:
            deduped.append((pos, marker))
    found_positions = deduped

    if not found_positions:
        return [("", text)]

    # Текст до первого маркера
    first_pos = found_positions[0][0]
    if first_pos > 50:
        parts.append(("", text[:first_pos].strip()))

    in_bibliography = False

    for i, (pos, marker) in enumerate(found_positions):
        next_pos = found_positions[i + 1][0] if i + 1 < len(found_positions) else len(text)

        # Если мы уже в библиографии — не разбиваем дальше
        if in_bibliography:
            continue

        # Найти конец строки заголовка
        end_of_line = text.find("\n", pos)
        if end_of_line < 0:
            end_of_line = len(text)
        heading = text[pos:end_of_line].strip()

        # Проверяем: это начало библиографии?
        if _is_bibliography_heading(heading):
            in_bibliography = True
            # Всё от этого маркера до конца — библиография
            body = text[end_of_line:].strip()
            parts.append((heading, body))
            continue

        body = text[end_of_line:next_pos].strip()
        parts.append((heading, body))

    return parts
