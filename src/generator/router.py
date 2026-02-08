"""Роутер генераторов — маппинг типа работы на соответствующий генератор."""

import logging
from typing import Optional, Callable, Awaitable

from src.generator import (
    essay, referat, coursework, diploma, homework,
    presentation, translation, copywriting, business_plan,
    practice_report, review, uniqueness,
)
from src.generator.essay import GenerationResult
from src.antiplagiat.checker import check_uniqueness, CheckResult
from src.antiplagiat.rewriter import rewrite_for_uniqueness

logger = logging.getLogger(__name__)

MAX_REWRITE_ATTEMPTS = 3

# Тип: async function(title, description, subject, pages, ...) -> GenerationResult
GeneratorFunc = Callable[..., Awaitable[GenerationResult]]

# Маппинг типа работы → генератор
# None означает: тип пока не поддерживается или требует ручной обработки
GENERATORS: dict[str, Optional[GeneratorFunc]] = {
    # Эссе и сочинения
    "Эссе": essay.generate,
    "Сочинение": essay.generate,
    "Аннотация": essay.generate,
    "Творческая работа": essay.generate,

    # Рефераты, доклады, статьи
    "Реферат": referat.generate,
    "Доклад": referat.generate,
    "Статья": referat.generate,
    "Автореферат": referat.generate,
    "Статья ВАК/Scopus": referat.generate,

    # Курсовые
    "Курсовая работа": coursework.generate,
    "Научно-исследовательская работа (НИР)": coursework.generate,
    "Индивидуальный проект": coursework.generate,
    "Маркетинговое исследование": coursework.generate,

    # Дипломные / ВКР
    "Выпускная квалификационная работа (ВКР)": diploma.generate,
    "Дипломная работа": diploma.generate,
    "Монография": diploma.generate,

    # Контрольные и задачи
    "Контрольная работа": homework.generate,
    "Решение задач": homework.generate,
    "Ответы на вопросы": homework.generate,
    "Лабораторная работа": homework.generate,
    "Другое": homework.generate,

    # Презентации
    "Презентации": presentation.generate,

    # Переводы
    "Перевод": translation.generate,

    # Копирайтинг
    "Копирайтинг": copywriting.generate,
    "Набор текста": copywriting.generate,

    # Повышение уникальности
    "Повышение уникальности текста": uniqueness.generate,
    "Гуманизация работы": uniqueness.generate,

    # Бизнес-план
    "Бизнес-план": business_plan.generate,

    # Отчёт по практике
    "Отчёт по практике": practice_report.generate,

    # Рецензии и вычитка
    "Рецензия": review.generate,
    "Вычитка и рецензирование работ": review.generate,
    "Проверка работы": review.generate,

    # Задачи по программированию (Этап 6 — sandbox)
    "Задача по программированию": None,

    # Не поддерживаемые (реалтайм)
    "Онлайн-консультация": None,
    "Помощь on-line": None,
    "Подбор темы работы": None,
    "Разбор отчёта Антиплагиат": None,
}


def get_generator(work_type: str) -> Optional[GeneratorFunc]:
    """Получить генератор для типа работы."""
    gen = GENERATORS.get(work_type)
    if gen is None:
        logger.warning("Генератор для '%s' не найден или не реализован", work_type)
    return gen


def is_supported(work_type: str) -> bool:
    """Проверить, поддерживается ли тип работы."""
    return GENERATORS.get(work_type) is not None


def supported_types() -> list[str]:
    """Получить список поддерживаемых типов работ."""
    return [k for k, v in GENERATORS.items() if v is not None]


async def generate_work(
    work_type: str,
    title: str,
    description: str = "",
    subject: str = "",
    pages: Optional[int] = None,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> Optional[GenerationResult]:
    """Сгенерировать работу по типу.

    Returns:
        GenerationResult или None если тип не поддерживается.
    """
    gen = get_generator(work_type)
    if gen is None:
        logger.error("Тип работы '%s' не поддерживается", work_type)
        return None

    # Если страницы не указаны — используем дефолт
    if pages is None:
        pages = _default_pages(work_type)

    result = await gen(
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
        font_size=font_size,
        line_spacing=line_spacing,
    )

    result.work_type = work_type
    return result


async def generate_and_check(
    work_type: str,
    title: str,
    description: str = "",
    subject: str = "",
    pages: Optional[int] = None,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
    antiplagiat_system: str = "textru",
) -> tuple[Optional[GenerationResult], Optional[CheckResult]]:
    """Сгенерировать работу и проверить уникальность.

    Если уникальность ниже порога — перефразирует текст (до 3 раз).

    Returns:
        (GenerationResult, CheckResult) или (None, None) если тип не поддерживается.
    """
    result = await generate_work(
        work_type=work_type,
        title=title,
        description=description,
        subject=subject,
        pages=pages,
        methodology_summary=methodology_summary,
        required_uniqueness=required_uniqueness,
        font_size=font_size,
        line_spacing=line_spacing,
    )

    if result is None:
        return None, None

    target = float(required_uniqueness) if required_uniqueness else 50.0

    # Проверяем уникальность
    try:
        check_result = await check_uniqueness(
            text=result.text,
            system=antiplagiat_system,
            required_uniqueness=target,
        )
    except Exception as e:
        logger.error("Ошибка проверки уникальности: %s", e)
        return result, None

    # Если уникальность достаточна — возвращаем
    if check_result.is_sufficient:
        logger.info("Уникальность %.1f%% >= %.1f%% — OK", check_result.uniqueness, target)
        return result, check_result

    # Рерайт-цикл: до MAX_REWRITE_ATTEMPTS попыток
    current_text = result.text
    current_uniqueness = check_result.uniqueness

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        logger.info(
            "Рерайт попытка %d/%d: текущая уникальность %.1f%%, цель %.1f%%",
            attempt, MAX_REWRITE_ATTEMPTS, current_uniqueness, target,
        )

        try:
            rewrite_result = await rewrite_for_uniqueness(
                text=current_text,
                target_percent=target,
                current_percent=current_uniqueness,
            )
            current_text = rewrite_result.text

            # Обновляем токены в результате
            result.text = current_text
            result.input_tokens += rewrite_result.input_tokens
            result.output_tokens += rewrite_result.output_tokens
            result.total_tokens += rewrite_result.total_tokens
            result.cost_usd += rewrite_result.cost_usd

            # Повторная проверка
            check_result = await check_uniqueness(
                text=current_text,
                system=antiplagiat_system,
                required_uniqueness=target,
            )
            current_uniqueness = check_result.uniqueness

            if check_result.is_sufficient:
                logger.info(
                    "Уникальность достигнута после %d рерайтов: %.1f%%",
                    attempt, current_uniqueness,
                )
                return result, check_result

        except Exception as e:
            logger.error("Ошибка при рерайте (попытка %d): %s", attempt, e)
            break

    logger.warning(
        "Уникальность %.1f%% после %d попыток рерайта (цель %.1f%%)",
        current_uniqueness, MAX_REWRITE_ATTEMPTS, target,
    )
    return result, check_result


def _default_pages(work_type: str) -> int:
    """Количество страниц по умолчанию."""
    defaults = {
        "Эссе": 5,
        "Сочинение": 5,
        "Аннотация": 2,
        "Творческая работа": 5,
        "Реферат": 15,
        "Доклад": 10,
        "Статья": 10,
        "Автореферат": 10,
        "Статья ВАК/Scopus": 10,
        "Курсовая работа": 30,
        "Научно-исследовательская работа (НИР)": 30,
        "Индивидуальный проект": 25,
        "Маркетинговое исследование": 30,
        "Выпускная квалификационная работа (ВКР)": 80,
        "Дипломная работа": 80,
        "Монография": 100,
        "Контрольная работа": 10,
        "Решение задач": 5,
        "Ответы на вопросы": 10,
        "Лабораторная работа": 10,
        "Презентации": 15,
        "Перевод": 10,
        "Копирайтинг": 5,
        "Набор текста": 10,
        "Повышение уникальности текста": 10,
        "Гуманизация работы": 10,
        "Бизнес-план": 25,
        "Отчёт по практике": 20,
        "Рецензия": 3,
        "Вычитка и рецензирование работ": 3,
        "Проверка работы": 3,
        "Другое": 10,
    }
    return defaults.get(work_type, 15)
