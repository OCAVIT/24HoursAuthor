"""Роутер генераторов — маппинг типа работы на соответствующий генератор."""

import logging
from typing import Optional, Callable, Awaitable

from src.generator import essay, referat
from src.generator.essay import GenerationResult

logger = logging.getLogger(__name__)

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

    # Курсовые (TODO: Этап 5)
    "Курсовая работа": None,
    "Научно-исследовательская работа (НИР)": None,
    "Индивидуальный проект": None,
    "Маркетинговое исследование": None,

    # Дипломные (TODO: Этап 5)
    "Выпускная квалификационная работа (ВКР)": None,
    "Дипломная работа": None,
    "Монография": None,

    # Контрольные и задачи (TODO: Этап 5)
    "Контрольная работа": None,
    "Решение задач": None,
    "Ответы на вопросы": None,
    "Лабораторная работа": None,

    # Другие типы (TODO: Этап 5)
    "Презентации": None,
    "Перевод": None,
    "Задача по программированию": None,
    "Копирайтинг": None,
    "Набор текста": None,
    "Повышение уникальности текста": None,
    "Гуманизация работы": None,
    "Бизнес-план": None,
    "Отчёт по практике": None,
    "Рецензия": None,
    "Вычитка и рецензирование работ": None,
    "Проверка работы": None,
    "Другое": None,

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
    }
    return defaults.get(work_type, 15)
