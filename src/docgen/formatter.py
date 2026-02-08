"""Утилиты форматирования текста по ГОСТ."""

import re


def normalize_text(text: str) -> str:
    """Нормализовать текст: лишние пробелы, переносы строк."""
    # Убираем множественные пробелы
    text = re.sub(r" {2,}", " ", text)
    # Убираем пробелы перед знаками препинания
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    # Нормализуем переносы строк (больше 2 подряд → 2)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def estimate_pages(text: str, chars_per_page: int = 1800) -> int:
    """Оценить количество страниц."""
    return max(1, len(text) // chars_per_page)


def split_into_paragraphs(text: str) -> list[str]:
    """Разбить текст на абзацы."""
    paragraphs = re.split(r"\n\n+", text)
    return [p.strip() for p in paragraphs if p.strip()]
