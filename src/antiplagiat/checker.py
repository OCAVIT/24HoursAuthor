"""Основной чекер уникальности — координирует проверку через разные сервисы."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Результат проверки уникальности."""
    uniqueness: float
    system: str
    is_sufficient: bool
    required: float
    text_length: int


def extract_text_from_docx(filepath: str | Path) -> str:
    """Извлечь текст из DOCX файла.

    Args:
        filepath: Путь к .docx файлу.

    Returns:
        Полный текст документа.
    """
    from docx import Document

    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    doc = Document(str(filepath))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


async def check_uniqueness(
    filepath: Optional[str | Path] = None,
    text: Optional[str] = None,
    system: str = "textru",
    required_uniqueness: Optional[float] = None,
) -> CheckResult:
    """Проверить уникальность текста или DOCX файла.

    Args:
        filepath: Путь к DOCX файлу (извлечёт текст автоматически).
        text: Текст для проверки (если файл не указан).
        system: Система антиплагиата ('textru' или 'etxt').
        required_uniqueness: Требуемый процент уникальности.

    Returns:
        CheckResult с результатами проверки.

    Raises:
        ValueError: Если не указан ни файл, ни текст.
        RuntimeError: При ошибке API.
    """
    if text is None and filepath is None:
        raise ValueError("Необходимо указать filepath или text")

    if text is None:
        text = extract_text_from_docx(filepath)

    if not text.strip():
        raise ValueError("Текст пуст")

    required = required_uniqueness or settings.min_uniqueness

    # Выбираем систему антиплагиата
    uniqueness = await _check_with_system(text, system)

    is_sufficient = uniqueness >= required

    logger.info(
        "Проверка уникальности [%s]: %.1f%% (требуется %.1f%%) — %s",
        system, uniqueness, required,
        "OK" if is_sufficient else "НЕДОСТАТОЧНО",
    )

    return CheckResult(
        uniqueness=uniqueness,
        system=system,
        is_sufficient=is_sufficient,
        required=required,
        text_length=len(text),
    )


async def _check_with_system(text: str, system: str) -> float:
    """Проверить текст через указанную систему."""
    if system == "textru":
        from src.antiplagiat.textru import check
        return await check(text)
    elif system == "etxt":
        from src.antiplagiat.etxt import check
        return await check(text)
    else:
        logger.warning("Неизвестная система '%s', используем textru", system)
        from src.antiplagiat.textru import check
        return await check(text)
