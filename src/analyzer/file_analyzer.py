"""Анализ прикреплённых файлов заказа (PDF, DOCX) — извлечение текста и суммаризация."""

import logging
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: Path) -> str:
    """Извлечь текст из PDF через PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(file_path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except Exception as e:
        logger.error("Ошибка извлечения текста из PDF %s: %s", file_path, e)
        return ""


def extract_text_from_docx(file_path: Path) -> str:
    """Извлечь текст из DOCX через python-docx."""
    try:
        from docx import Document
        doc = Document(str(file_path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        logger.error("Ошибка извлечения текста из DOCX %s: %s", file_path, e)
        return ""


def extract_text(file_path: Path) -> str:
    """Извлечь текст из файла (определяет тип по расширению)."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        return extract_text_from_docx(file_path)
    elif suffix == ".txt":
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception:
            return file_path.read_text(encoding="cp1251", errors="ignore")
    else:
        logger.warning("Неподдерживаемый формат файла: %s", suffix)
        return ""


async def summarize_files(file_paths: list[Path]) -> Optional[dict]:
    """Извлечь и суммаризировать содержимое прикреплённых файлов.

    Returns:
        {
            "summary": str,
            "requirements": str,
            "structure": str,
            "volume": str,
            "raw_text": str (усечённый),
            "input_tokens": int,
            "output_tokens": int,
            "cost_usd": float,
        }
    """
    all_text = []
    for fp in file_paths:
        text = extract_text(fp)
        if text.strip():
            all_text.append(f"--- Файл: {fp.name} ---\n{text}")

    if not all_text:
        return None

    combined = "\n\n".join(all_text)
    # Ограничиваем текст для API (макс ~12000 символов ≈ 3000 токенов)
    if len(combined) > 12000:
        combined = combined[:12000] + "\n... (текст обрезан)"

    result = await chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты анализируешь методические указания и приложенные файлы к заказу. "
                    "Извлеки ключевую информацию для написания работы.\n\n"
                    "Ответь структурированно:\n"
                    "1. КРАТКОЕ СОДЕРЖАНИЕ: о чём методичка/файл\n"
                    "2. ТРЕБОВАНИЯ К ОФОРМЛЕНИЮ: шрифт, интервал, поля, нумерация и т.д.\n"
                    "3. СТРУКТУРА РАБОТЫ: какие разделы/главы нужны\n"
                    "4. ОБЪЁМ: сколько страниц, символов или слов"
                ),
            },
            {"role": "user", "content": f"Проанализируй следующие файлы:\n\n{combined}"},
        ],
        model=settings.openai_model_fast,
        temperature=0.2,
        max_tokens=1024,
    )

    content = result["content"]

    # Разбираем ответ на секции
    summary = content
    requirements = ""
    structure = ""
    volume = ""

    sections = content.split("\n")
    current_section = "summary"
    section_lines: dict[str, list[str]] = {"summary": [], "requirements": [], "structure": [], "volume": []}

    for line in sections:
        lower = line.lower()
        if "требовани" in lower or "оформлени" in lower:
            current_section = "requirements"
        elif "структур" in lower or "раздел" in lower or "глав" in lower:
            current_section = "structure"
        elif "объём" in lower or "объем" in lower or "страниц" in lower:
            current_section = "volume"
        section_lines[current_section].append(line)

    return {
        "summary": "\n".join(section_lines["summary"]).strip(),
        "requirements": "\n".join(section_lines["requirements"]).strip(),
        "structure": "\n".join(section_lines["structure"]).strip(),
        "volume": "\n".join(section_lines["volume"]).strip(),
        "raw_text": combined[:5000],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
    }
