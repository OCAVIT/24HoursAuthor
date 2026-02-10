"""Расчёт оптимальной ставки для заказа.

Комиссия платформы (проверено февраль 2026):
- Автор получает 97.5% от ставки (2.5% комиссия)
- Заказчик платит ~2.0-2.3x от ставки (скользящая шкала)
- Поле «Бюджет заказчика» на странице = целевая ставка автора (не итог для клиента)
"""

import logging
import random
from typing import Optional

from src.scraper.order_detail import OrderDetail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Комиссия платформы (verified Feb 2026 via bid form probe)
# ---------------------------------------------------------------------------
AUTHOR_COMMISSION_RATE = 0.975  # автор получает 97.5% (2.5% забирает платформа)

# ---------------------------------------------------------------------------
# Оценка стоимости API по типу работы (руб.) — для profitability gate
# Включает генерацию + скоринг + чат + возможный рерайт
# ---------------------------------------------------------------------------
ESTIMATED_API_COST_RUB: dict[str, int] = {
    "Эссе": 12,
    "Сочинение": 12,
    "Аннотация": 8,
    "Творческая работа": 12,
    "Реферат": 25,
    "Доклад": 20,
    "Статья": 25,
    "Автореферат": 20,
    "Статья ВАК/Scopus": 35,
    "Курсовая работа": 90,
    "Научно-исследовательская работа (НИР)": 90,
    "Индивидуальный проект": 90,
    "Маркетинговое исследование": 90,
    "Дипломная работа": 220,
    "Выпускная квалификационная работа (ВКР)": 220,
    "Монография": 300,
    "Контрольная работа": 18,
    "Решение задач": 12,
    "Ответы на вопросы": 12,
    "Лабораторная работа": 20,
    "Перевод": 15,
    "Копирайтинг": 10,
    "Набор текста": 5,
    "Повышение уникальности текста": 15,
    "Гуманизация работы": 15,
    "Бизнес-план": 80,
    "Отчёт по практике": 50,
    "Рецензия": 15,
    "Вычитка и рецензирование работ": 15,
    "Проверка работы": 12,
    "Задача по программированию": 20,
    "Другое": 30,
}

# Минимальный множитель прибыльности: доход >= api_cost * MIN_PROFIT_MULTIPLIER
MIN_PROFIT_MULTIPLIER = 3

# ---------------------------------------------------------------------------
# Базовые цены за страницу (руб.) — для формульного расчёта
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def estimate_income(bid_price: int) -> int:
    """Рассчитать реальный доход автора после комиссии платформы (2.5%)."""
    return int(bid_price * AUTHOR_COMMISSION_RATE)


def estimate_api_cost(work_type: str) -> int:
    """Оценочная стоимость API (руб.) для данного типа работы."""
    return ESTIMATED_API_COST_RUB.get(work_type, 30)


def is_profitable(bid_price: int, work_type: str) -> bool:
    """Проверить, оправдывает ли доход вложения в API.

    Доход должен быть >= api_cost * MIN_PROFIT_MULTIPLIER (3x).
    """
    income = estimate_income(bid_price)
    cost = estimate_api_cost(work_type)
    return income >= cost * MIN_PROFIT_MULTIPLIER


def min_profitable_bid(work_type: str) -> int:
    """Минимальная ставка, при которой заказ прибылен."""
    cost = estimate_api_cost(work_type)
    # income = bid * 0.975 >= cost * 3  →  bid >= cost * 3 / 0.975
    min_bid = int(cost * MIN_PROFIT_MULTIPLIER / AUTHOR_COMMISSION_RATE) + 1
    return max(MIN_BID, min_bid)


# ---------------------------------------------------------------------------
# Main pricing logic
# ---------------------------------------------------------------------------

def calculate_price(order: OrderDetail) -> int:
    """Рассчитать оптимальную ставку в рублях.

    Приоритет источников цены:
    1. Бюджет заказчика + средняя ставка (взвешенное среднее)
    2. Только бюджет (85-95% от бюджета)
    3. Только средняя ставка (85-98% от средней)
    4. Формульный расчёт (base_price * pages * complexity)

    Финальная цена всегда >= MIN_BID и >= min_profitable_bid.
    """
    price = _combined_price(order)
    if price is None:
        price = _try_budget_based(order)
    if price is None:
        price = _try_average_bid_based(order)
    if price is None:
        price = _formula_based(order)

    # Не ниже минимума и не ниже порога прибыльности
    floor = min_profitable_bid(order.work_type or "Другое")
    price = max(floor, price)

    logger.info(
        "Цена для заказа %s (%s): %d руб. (доход ≈%d, API ≈%d)",
        order.order_id, order.work_type, price,
        estimate_income(price), estimate_api_cost(order.work_type or "Другое"),
    )
    return price


# ---------------------------------------------------------------------------
# Pricing strategies
# ---------------------------------------------------------------------------

def _combined_price(order: OrderDetail) -> Optional[int]:
    """Бюджет + средняя ставка → взвешенное среднее.

    Берём 60% от бюджета и 40% от средней ставки (бюджет важнее).
    Результат чуть ниже обоих, чтобы быть конкурентными.
    """
    if not order.budget_rub or not order.average_bid:
        return None
    blended = order.budget_rub * 0.6 + order.average_bid * 0.4
    factor = random.uniform(0.85, 0.95)
    return max(MIN_BID, int(blended * factor))


def _try_budget_based(order: OrderDetail) -> Optional[int]:
    """Рассчитать цену на основе бюджета заказчика (85-95%)."""
    if not order.budget_rub or order.budget_rub <= 0:
        return None
    factor = random.uniform(0.85, 0.95)
    return max(MIN_BID, int(order.budget_rub * factor))


def _try_average_bid_based(order: OrderDetail) -> Optional[int]:
    """Рассчитать цену на основе средней ставки (85-98%).

    Ставим чуть ниже средней, чтобы быть конкурентными.
    """
    if not order.average_bid or order.average_bid <= 0:
        return None
    factor = random.uniform(0.85, 0.98)
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
