"""Скоринг заказов — оценка привлекательности и выполнимости через GPT-4o-mini."""

import logging
from dataclasses import dataclass
from typing import Optional

from src.ai_client import chat_completion_json
from src.config import settings
from src.scraper.order_detail import OrderDetail

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    """Результат скоринга заказа."""
    score: int  # 0-100
    can_do: bool
    estimated_time_min: int
    estimated_cost_rub: float
    reason: str
    # Метаданные API-вызова
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


SCORING_SYSTEM_PROMPT = """Ты — AI-ассистент, оценивающий заказы на платформе Автор24.
Ты должен оценить, стоит ли браться за заказ и может ли AI выполнить его качественно.

Критерии скоринга (score 0-100):
- Тип работы: эссе/реферат = +30, курсовая = +20, диплом = +10, код = +15
- Предмет: гуманитарные = +20, экономические = +15, технические = +5
- Цена: выше средней для типа = +20, ниже = -10
- Дедлайн: >3 дней = +10, <24ч = -20
- Конкуренция: 0 ставок = +20, >5 ставок = -15
- Уникальность: <60% = +10, >80% = -5
- Наличие файлов/методички: +10

can_do = false для:
- Чертежей, 3D-моделирование, лабораторные с физическим оборудованием
- Заказы требующие реальных экспериментов
- Заказы на языках кроме русского/английского (кроме переводов)

Ответь строго в JSON формате:
{
  "score": число 0-100,
  "can_do": true/false,
  "estimated_time_min": число (оценка времени генерации в минутах),
  "estimated_cost_rub": число (примерная себестоимость в рублях на API),
  "reason": "краткое объяснение оценки"
}"""


def _build_order_prompt(order: OrderDetail) -> str:
    """Построить промпт с данными заказа."""
    parts = [
        f"Тип работы: {order.work_type}",
        f"Предмет: {order.subject}",
        f"Тема: {order.title}",
    ]

    if order.description:
        desc = order.description[:1000]
        parts.append(f"Описание: {desc}")

    if order.budget:
        parts.append(f"Бюджет заказчика: {order.budget} руб.")

    if order.pages_min:
        pages = f"{order.pages_min}"
        if order.pages_max and order.pages_max != order.pages_min:
            pages += f"-{order.pages_max}"
        parts.append(f"Количество страниц: {pages}")

    if order.required_uniqueness:
        parts.append(f"Требуемая уникальность: {order.required_uniqueness}%")

    if order.antiplagiat_system:
        parts.append(f"Система антиплагиата: {order.antiplagiat_system}")

    if order.deadline:
        parts.append(f"Дедлайн: {order.deadline}")

    if order.average_bid:
        parts.append(f"Средняя ставка: {order.average_bid} руб.")

    if order.file_names:
        parts.append(f"Прикреплённых файлов: {len(order.file_names)}")

    return "\n".join(parts)


async def score_order(order: OrderDetail) -> ScoreResult:
    """Оценить заказ через GPT-4o-mini."""
    user_prompt = _build_order_prompt(order)

    result = await chat_completion_json(
        messages=[
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.openai_model_fast,
        temperature=0.2,
        max_tokens=512,
    )

    data = result["data"]

    score_result = ScoreResult(
        score=min(100, max(0, int(data.get("score", 0)))),
        can_do=bool(data.get("can_do", False)),
        estimated_time_min=int(data.get("estimated_time_min", 60)),
        estimated_cost_rub=float(data.get("estimated_cost_rub", 10)),
        reason=str(data.get("reason", "Нет данных")),
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=result["cost_usd"],
    )

    logger.info(
        "Скоринг заказа %s: score=%d, can_do=%s, reason=%s",
        order.order_id, score_result.score, score_result.can_do, score_result.reason,
    )

    return score_result
