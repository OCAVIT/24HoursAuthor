"""Сборка DOCX файлов через Node.js subprocess (docx-js)."""

import asyncio
import json
import logging
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
        level = 1
        if any(kw in heading.lower() for kw in ["подраздел", "."]):
            level = 2

        sections.append({
            "heading": heading,
            "text": body.strip(),
            "level": level,
        })

    if not sections:
        sections.append({"heading": "", "text": text, "level": 1})

    return sections


def _sections_from_text(text: str) -> list[dict]:
    """Разбить текст на секции по обнаруженным заголовкам."""
    import re

    # Паттерны заголовков
    heading_patterns = [
        r"^(ВВЕДЕНИЕ|ЗАКЛЮЧЕНИЕ|СПИСОК ЛИТЕРАТУРЫ|СОДЕРЖАНИЕ)$",
        r"^(Введение|Заключение|Список литературы|Содержание)$",
        r"^(Глава\s+\d+[.\s]*.*)$",
        r"^(ГЛАВА\s+\d+[.\s]*.*)$",
        r"^(\d+\.\s+.+)$",
        r"^(\d+\.\d+\.\s+.+)$",
    ]
    combined_pattern = "|".join(heading_patterns)

    lines = text.split("\n")
    sections = []
    current_heading = ""
    current_body_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and re.match(combined_pattern, stripped, re.MULTILINE):
            if current_heading or current_body_lines:
                sections.append({
                    "heading": current_heading,
                    "text": "\n".join(current_body_lines).strip(),
                    "level": 2 if "." in current_heading[:5] else 1,
                })
            current_heading = stripped
            current_body_lines = []
        else:
            current_body_lines.append(line)

    # Последняя секция
    if current_heading or current_body_lines:
        sections.append({
            "heading": current_heading,
            "text": "\n".join(current_body_lines).strip(),
            "level": 2 if "." in current_heading[:5] else 1,
        })

    if not sections:
        sections.append({"heading": "", "text": text, "level": 1})

    return sections


def _split_by_markers(text: str, markers: list[str]) -> list[tuple[str, str]]:
    """Разбить текст по маркерам-заголовкам."""
    parts = []
    remaining = text

    found_positions: list[tuple[int, str]] = []
    text_lower = text.lower()
    for marker in markers:
        pos = text_lower.find(marker.lower())
        if pos >= 0:
            found_positions.append((pos, marker))

    # Сортируем по позиции
    found_positions.sort(key=lambda x: x[0])

    if not found_positions:
        return [("", text)]

    # Текст до первого маркера
    first_pos = found_positions[0][0]
    if first_pos > 50:
        parts.append(("", text[:first_pos].strip()))

    for i, (pos, marker) in enumerate(found_positions):
        next_pos = found_positions[i + 1][0] if i + 1 < len(found_positions) else len(text)
        # Найти конец строки заголовка
        end_of_line = text.find("\n", pos)
        if end_of_line < 0:
            end_of_line = len(text)
        heading = text[pos:end_of_line].strip()
        body = text[end_of_line:next_pos].strip()
        parts.append((heading, body))

    return parts
