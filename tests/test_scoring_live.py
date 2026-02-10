"""Live-тест скоринга через реальный OpenAI API.

Проверяет что обновлённый промпт корректно оценивает:
- Задачи по программированию на разных предметах → can_do=True, score >= 60
- Чертежи / 3D-моделирование → can_do=False
"""

import pytest

from src.scraper.order_detail import OrderDetail
from src.analyzer.order_scorer import score_order


def _make_order(**kwargs) -> OrderDetail:
    defaults = {
        "order_id": "99999",
        "title": "Тестовый заказ",
        "url": "https://avtor24.ru/order/99999",
        "work_type": "Эссе",
        "subject": "Философия",
        "description": "",
        "budget": "2000₽",
        "budget_rub": 2000,
        "deadline": "20.02.2026",
        "average_bid": 1500,
    }
    defaults.update(kwargs)
    return OrderDetail(**defaults)


# ─── Программирование: должно проходить ───


@pytest.mark.asyncio
async def test_programming_cs():
    """Программирование по информатике → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Информатика",
        title="Реализовать алгоритм сортировки на Python",
        description="Реализовать быструю сортировку и сортировку слиянием. Сравнить время работы.",
        budget="1500₽", budget_rub=1500,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для программирования по информатике: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60 для программирования: {result.reason}"


@pytest.mark.asyncio
async def test_programming_math():
    """Программирование по математике → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Математика",
        title="Программа для численного решения системы ОДУ методом Рунге-Кутты",
        description="Написать программу на Python для решения системы ОДУ. Построить графики.",
        budget="2000₽", budget_rub=2000,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для программирования по математике: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


@pytest.mark.asyncio
async def test_programming_economics():
    """Программирование по экономике → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Экономика",
        title="Программа расчёта NPV и IRR инвестиционного проекта",
        description="Написать программу на Python для расчёта показателей эффективности инвестиций.",
        budget="1800₽", budget_rub=1800,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для программирования по экономике: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


@pytest.mark.asyncio
async def test_programming_physics():
    """Программирование по физике → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Физика",
        title="Моделирование движения тела в гравитационном поле на C++",
        description="Смоделировать движение тела, построить траекторию. Язык: C++.",
        budget="2500₽", budget_rub=2500,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для программирования по физике: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


@pytest.mark.asyncio
async def test_programming_databases():
    """Программирование — базы данных → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Информационные технологии",
        title="Разработка базы данных библиотеки на SQL",
        description="Спроектировать и реализовать БД библиотеки. SQL запросы, триггеры, представления.",
        budget="2000₽", budget_rub=2000,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для задачи по БД: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


@pytest.mark.asyncio
async def test_programming_web():
    """Веб-разработка → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Задача по программированию",
        subject="Веб-программирование",
        title="Создать REST API на Node.js для интернет-магазина",
        description="REST API с авторизацией, CRUD для товаров, корзина. Node.js + Express + MongoDB.",
        budget="3000₽", budget_rub=3000,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для веб-разработки: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


# ─── Обычные работы: тоже должны проходить ───


@pytest.mark.asyncio
async def test_essay_philosophy():
    """Эссе по философии → can_do=True, score >= 60."""
    order = _make_order(
        work_type="Эссе",
        subject="Философия",
        title="Свобода воли в философии Канта",
        budget="1200₽", budget_rub=1200,
    )
    result = await score_order(order)
    assert result.can_do is True, f"can_do=False для эссе: {result.reason}"
    assert result.score >= 60, f"score={result.score} < 60: {result.reason}"


# ─── Должны отклоняться (can_do=False) ───


@pytest.mark.asyncio
async def test_drawing_rejected():
    """Чертёж → can_do=False."""
    order = _make_order(
        work_type="Чертёж",
        subject="Инженерная графика",
        title="Чертёж детали в AutoCAD",
        description="Выполнить чертёж детали по ГОСТ в AutoCAD.",
        budget="2000₽", budget_rub=2000,
    )
    result = await score_order(order)
    assert result.can_do is False, f"can_do=True для чертежа — должно быть False: {result.reason}"


@pytest.mark.asyncio
async def test_3d_modeling_rejected():
    """3D-моделирование → can_do=False."""
    order = _make_order(
        work_type="Другое",
        subject="Дизайн",
        title="3D-модель здания в Blender",
        description="Создать 3D-модель жилого здания с текстурами в Blender.",
        budget="5000₽", budget_rub=5000,
    )
    result = await score_order(order)
    assert result.can_do is False, f"can_do=True для 3D-моделирования — должно быть False: {result.reason}"
