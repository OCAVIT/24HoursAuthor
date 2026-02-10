"""Основной чекер уникальности — координирует проверку через разные сервисы.

Оптимизация API-расхода:
- Сначала проверяем 2-3 случайных фрагмента по 1500-2000 символов из середины работы
- Если средняя уникальность выборок >= порог + 5% — считаем ОК, полную проверку не делаем
- Если < порога + 5% — тогда полная проверка
"""

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

# Параметры выборочной проверки
SAMPLE_SIZE = 3                 # Количество фрагментов
SAMPLE_CHARS_MIN = 1500         # Мин символов на фрагмент
SAMPLE_CHARS_MAX = 2000         # Макс символов на фрагмент
SAMPLE_MARGIN = 5.0             # Запас сверх порога для прохождения по выборке
MIN_TEXT_FOR_SAMPLING = 5000    # Мин длина текста для выборочной проверки


@dataclass
class CheckResult:
    """Результат проверки уникальности."""
    uniqueness: float
    system: str
    is_sufficient: bool
    required: float
    text_length: int
    is_sampled: bool = False     # True = результат по выборке (не полный текст)


def extract_text_from_docx(filepath: str | Path) -> str:
    """Извлечь текст из DOCX файла."""
    from docx import Document

    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    doc = Document(str(filepath))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_samples(text: str, n: int = SAMPLE_SIZE) -> list[str]:
    """Извлечь N случайных фрагментов из середины текста.

    Избегает введения (~15% текста) и заключения (~15% текста),
    т.к. они шаблонные и дают завышенный/заниженный процент.
    """
    total = len(text)
    # Зона выборки: 15%-85% текста (пропускаем введение и заключение)
    start_zone = int(total * 0.15)
    end_zone = int(total * 0.85)
    zone_text = text[start_zone:end_zone]
    zone_len = len(zone_text)

    if zone_len < SAMPLE_CHARS_MIN * n:
        # Текст слишком короткий — берём всю середину одним куском
        return [zone_text]

    samples = []
    used_ranges: list[tuple[int, int]] = []

    for _ in range(n):
        sample_len = random.randint(SAMPLE_CHARS_MIN, SAMPLE_CHARS_MAX)
        max_start = zone_len - sample_len

        # Пытаемся найти непересекающийся фрагмент
        for attempt in range(20):
            start = random.randint(0, max_start)
            end = start + sample_len

            # Проверяем пересечение с уже взятыми
            overlaps = any(
                not (end <= us or start >= ue)
                for us, ue in used_ranges
            )
            if not overlaps:
                # Выравниваем на границу предложения
                dot_pos = zone_text.rfind(". ", max(0, start - 100), start + 50)
                if dot_pos > 0:
                    start = dot_pos + 2

                dot_end = zone_text.find(". ", end - 50, end + 100)
                if dot_end > 0:
                    end = dot_end + 1

                samples.append(zone_text[start:end])
                used_ranges.append((start, end))
                break

    return samples


async def check_uniqueness(
    filepath: Optional[str | Path] = None,
    text: Optional[str] = None,
    system: str = "textru",
    required_uniqueness: Optional[float] = None,
) -> CheckResult:
    """Проверить уникальность текста или DOCX файла.

    Оптимизация: сначала выборочная проверка (2-3 фрагмента).
    Если средняя >= порог + 5% — считаем ОК без полной проверки.
    """
    if text is None and filepath is None:
        raise ValueError("Необходимо указать filepath или text")

    if text is None:
        text = extract_text_from_docx(filepath)

    if not text.strip():
        raise ValueError("Текст пуст")

    required = required_uniqueness or settings.min_uniqueness

    # --- Выборочная проверка (если текст достаточно длинный) ---
    if len(text) >= MIN_TEXT_FOR_SAMPLING:
        samples = _extract_samples(text, SAMPLE_SIZE)

        if samples:
            sample_scores = []
            for i, sample in enumerate(samples, 1):
                try:
                    score = await _check_with_system(sample, system)
                    sample_scores.append(score)
                    logger.info(
                        "Выборка %d/%d: %.1f%% (%d символов)",
                        i, len(samples), score, len(sample),
                    )
                except Exception as e:
                    logger.warning("Ошибка проверки выборки %d: %s", i, e)

            if sample_scores:
                avg_score = sum(sample_scores) / len(sample_scores)
                threshold = required + SAMPLE_MARGIN

                if avg_score >= threshold:
                    logger.info(
                        "Выборочная проверка: %.1f%% (порог %.1f%%) — "
                        "полная проверка не требуется",
                        avg_score, required,
                    )
                    return CheckResult(
                        uniqueness=avg_score,
                        system=system,
                        is_sufficient=True,
                        required=required,
                        text_length=len(text),
                        is_sampled=True,
                    )
                else:
                    logger.info(
                        "Выборочная проверка: %.1f%% (порог %.1f%%) — "
                        "запускаю полную проверку",
                        avg_score, required,
                    )

    # --- Полная проверка ---
    uniqueness = await _check_with_system(text, system)
    is_sufficient = uniqueness >= required

    logger.info(
        "Полная проверка уникальности [%s]: %.1f%% (требуется %.1f%%) — %s",
        system, uniqueness, required,
        "OK" if is_sufficient else "НЕДОСТАТОЧНО",
    )

    return CheckResult(
        uniqueness=uniqueness,
        system=system,
        is_sufficient=is_sufficient,
        required=required,
        text_length=len(text),
        is_sampled=False,
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
