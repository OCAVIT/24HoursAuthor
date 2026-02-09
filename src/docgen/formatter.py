"""Утилиты форматирования текста по ГОСТ."""

import re


def strip_markdown(text: str) -> str:
    """Убрать markdown-разметку из текста."""
    # Заголовки: # Текст, ## Текст, ### Текст
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold: **текст** → текст
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # Italic: *текст* → текст (но не **, уже убрали)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    # Маркированные списки: - текст или * текст в начале строки
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    # Цитаты: > текст
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Inline code: `текст`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Code blocks: ```...```
    text = re.sub(r"```[\s\S]*?```", "", text)
    return text


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
