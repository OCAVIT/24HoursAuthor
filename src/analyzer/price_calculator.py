"""Расчёт оптимальной ставки для заказа."""

import logging
import random
from typing import Optional

from src.scraper.order_detail import OrderDetail

logger = logging.getLogger(__name__)

# Базовые цены за страницу (руб.)
BASE_PRICE_PER_PAGE = {
    "Эссе": 150,
    "Сочинение": 150,
    "Реферат": 120,
    "Доклад": 120,
    "Статья": 120,
    "Автореферат": 120,
    "Аннотация": 150,
    "Курсовая работа": 200,
    "Научно-исследовательская работа (НИР)": 200,
    "Индивидуальный проект": 200,
    "Маркетинговое исследование": 200,
    "Дипломная работа": 250,
    "Выпускная квалификационная работа (ВКР)": 250,
    "Монография": 250,
    "Контрольная работа": 100,
    "Решение задач": 300,
    "Ответы на вопросы": 100,
    "Лабораторная работа": 150,
    "Презентации": 100,
    "Перевод": 100,
    "Копирайтинг": 80,
    "Набор текста": 50,
    "Бизнес-план": 200,
    "Отчёт по практике": 150,
    "Рецензия": 120,
    "Вычитка и рецензирование работ": 100,
    "Повышение уникальности текста": 80,
    "Гуманизация работы": 80,
    "Проверка работы": 100,
    "Творческая работа": 150,
    "Статья ВАК/Scopus": 200,
    "Другое": 150,
}

# Фиксированные цены для задач без постраничной оценки (руб.)
FIXED_PRICE = {
    "Задача по программированию": (500, 3000),
    "Решение задач": (100, 500),
}

MIN_BID = 300  # Минимальная ставка


def calculate_price(order: OrderDetail) -> int:
    """Рассчитать оптимальную ставку в рублях.

    Логика:
    1. Если указан бюджет заказчика — 85-95% от бюджета
    2. Если есть средняя ставка — 90-100% от средней
    3. Если ничего нет — расчёт по формуле
    """
    price = _try_budget_based(order)
    if price is None:
        price = _try_average_bid_based(order)
    if price is None:
        price = _formula_based(order)

    price = max(MIN_BID, price)

    logger.info(
        "Цена для заказа %s (%s): %d руб.",
        order.order_id, order.work_type, price,
    )
    return price


def _try_budget_based(order: OrderDetail) -> Optional[int]:
    """Рассчитать цену на основе бюджета заказчика."""
    if not order.budget or order.budget <= 0:
        return None
    factor = random.uniform(0.85, 0.95)
    return max(MIN_BID, int(order.budget * factor))


def _try_average_bid_based(order: OrderDetail) -> Optional[int]:
    """Рассчитать цену на основе средней ставки."""
    if not order.average_bid or order.average_bid <= 0:
        return None
    factor = random.uniform(0.90, 1.00)
    return max(MIN_BID, int(order.average_bid * factor))


def _formula_based(order: OrderDetail) -> int:
    """Рассчитать цену по формуле: base_price * pages * complexity."""
    work_type = order.work_type or "Другое"

    # Фиксированная цена для определённых типов
    if work_type in FIXED_PRICE:
        lo, hi = FIXED_PRICE[work_type]
        return random.randint(lo, hi)

    base = BASE_PRICE_PER_PAGE.get(work_type, 150)
    pages = order.pages_max or order.pages_min or _default_pages(work_type)

    complexity = _complexity_factor(order)
    price = int(base * pages * complexity)

    return max(MIN_BID, price)


def _default_pages(work_type: str) -> int:
    """Количество страниц по умолчанию для типа работы."""
    defaults = {
        "Эссе": 5,
        "Сочинение": 5,
        "Реферат": 15,
        "Доклад": 10,
        "Курсовая работа": 30,
        "Дипломная работа": 80,
        "Выпускная квалификационная работа (ВКР)": 80,
        "Контрольная работа": 10,
        "Презентации": 15,
        "Отчёт по практике": 25,
        "Бизнес-план": 30,
        "Рецензия": 5,
        "Аннотация": 2,
    }
    return defaults.get(work_type, 15)


def _complexity_factor(order: OrderDetail) -> float:
    """Коэффициент сложности на основе требований."""
    factor = 1.0

    # Высокая уникальность сложнее
    if order.required_uniqueness and order.required_uniqueness > 80:
        factor += 0.15
    elif order.required_uniqueness and order.required_uniqueness > 70:
        factor += 0.05

    # Наличие методички — точнее ТЗ, но и требования строже
    if order.file_names:
        factor += 0.05

    return factor
