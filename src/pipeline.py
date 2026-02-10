"""Пайплайн обработки заказа: анализ → генерация → DOCX."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.analyzer.order_scorer import score_order, ScoreResult
from src.analyzer.price_calculator import (
    calculate_price, is_profitable, estimate_income, estimate_api_cost,
)
from src.analyzer.file_analyzer import summarize_files
from src.config import settings
from src.database.crud import (
    create_order, update_order_status, track_api_usage, create_action_log,
    get_order_by_avtor24_id,
)
from src.docgen.builder import build_docx
from src.generator.router import generate_work, is_supported
from src.notifications.events import push_notification
from src.scraper.order_detail import OrderDetail

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Результат обработки заказа через пайплайн."""
    order_id: str
    db_order_id: Optional[int] = None
    score: Optional[ScoreResult] = None
    bid_price: Optional[int] = None
    generated_file: Optional[Path] = None
    error: Optional[str] = None
    status: str = "new"


async def analyze_and_bid(
    session: AsyncSession,
    order: OrderDetail,
    min_score: int = 60,
) -> PipelineResult:
    """Фаза 1: Анализ заказа и постановка ставки.

    1. Проверяем дубликат в БД
    2. Скоринг через GPT-4o-mini
    3. Расчёт оптимальной цены
    4. Сохранение в БД

    Returns:
        PipelineResult с score и bid_price.
    """
    result = PipelineResult(order_id=order.order_id)

    # Проверяем дубликат
    existing = await get_order_by_avtor24_id(session, order.order_id)
    if existing:
        result.db_order_id = existing.id
        result.status = existing.status
        result.error = "Заказ уже обработан"
        return result

    # Gate: пропускаем заказы с архивами (не можем распаковать/анализировать)
    _ARCHIVE_EXTS = {".rar", ".zip", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tar.gz", ".tgz"}
    archive_files = [
        f for f in (order.file_names or [])
        if any(f.lower().endswith(ext) for ext in _ARCHIVE_EXTS)
    ]
    if archive_files:
        logger.info("Пропуск заказа %s: содержит архивы: %s", order.order_id, archive_files)
        db_order = await create_order(
            session,
            avtor24_id=order.order_id,
            title=order.title,
            work_type=order.work_type,
            subject=order.subject,
            description=order.description,
            budget_rub=order.budget,
            score=0,
            status="rejected",
        )
        result.db_order_id = db_order.id
        result.status = "rejected"
        result.error = f"Пропущен: архивы ({', '.join(archive_files)})"
        return result

    # Gate: пропускаем презентации (нет функционала генерации .pptx)
    _title_lower = (order.title or "").lower()
    _desc_lower = (order.description or "").lower()
    _wtype_lower = (order.work_type or "").lower()
    _is_presentation = (
        "презентаци" in _title_lower
        or "презентаци" in _desc_lower
        or "презентаци" in _wtype_lower
    )
    if _is_presentation:
        logger.info("Пропуск заказа %s: презентация (нет .pptx генератора)", order.order_id)
        db_order = await create_order(
            session,
            avtor24_id=order.order_id,
            title=order.title,
            work_type=order.work_type,
            subject=order.subject,
            description=order.description,
            budget_rub=order.budget,
            score=0,
            status="rejected",
        )
        result.db_order_id = db_order.id
        result.status = "rejected"
        result.error = "Пропущен: презентация (нет .pptx генератора)"
        return result

    # Скоринг
    score = await score_order(order)
    result.score = score

    # Трекинг API
    await track_api_usage(
        session, model=settings.openai_model_fast, purpose="scoring",
        input_tokens=score.input_tokens, output_tokens=score.output_tokens,
        cost_usd=score.cost_usd,
    )

    await create_action_log(
        session, action="score",
        details=f"score={score.score}, can_do={score.can_do}, reason={score.reason}",
    )

    if not score.can_do or score.score < min_score:
        # Сохраняем в БД со статусом rejected
        db_order = await create_order(
            session,
            avtor24_id=order.order_id,
            title=order.title,
            work_type=order.work_type,
            subject=order.subject,
            description=order.description,
            budget_rub=order.budget,
            score=score.score,
            status="rejected",
        )
        result.db_order_id = db_order.id
        result.status = "rejected"
        result.error = f"Не подходит: {score.reason}"
        return result

    # Расчёт цены
    bid_price = calculate_price(order)
    result.bid_price = bid_price

    # Gate: проверка прибыльности (доход >= API cost * 3)
    if not is_profitable(bid_price, order.work_type or "Другое"):
        income = estimate_income(bid_price)
        api_cost = estimate_api_cost(order.work_type or "Другое")
        logger.info(
            "Пропуск заказа %s: не прибыльный (ставка=%d, доход=%d, API≈%d)",
            order.order_id, bid_price, income, api_cost,
        )
        db_order = await create_order(
            session,
            avtor24_id=order.order_id,
            title=order.title,
            work_type=order.work_type,
            subject=order.subject,
            description=order.description,
            budget_rub=order.budget,
            bid_price=bid_price,
            score=score.score,
            status="rejected",
        )
        result.db_order_id = db_order.id
        result.status = "rejected"
        result.error = f"Не прибыльный: доход {income}₽ < API {api_cost}₽ × 3"
        return result

    # Сохраняем в БД
    db_order = await create_order(
        session,
        avtor24_id=order.order_id,
        title=order.title,
        work_type=order.work_type,
        subject=order.subject,
        description=order.description,
        pages_min=order.pages_min,
        pages_max=order.pages_max,
        font_size=order.font_size,
        line_spacing=order.line_spacing,
        required_uniqueness=order.required_uniqueness,
        antiplagiat_system=order.antiplagiat_system,
        deadline=None,  # TODO: парсить дату
        budget_rub=order.budget,
        bid_price=bid_price,
        score=score.score,
        status="scored",
    )
    result.db_order_id = db_order.id
    result.status = "scored"

    return result


async def generate_and_build(
    session: AsyncSession,
    order_db_id: int,
    order: OrderDetail,
    downloaded_files: Optional[list[Path]] = None,
) -> PipelineResult:
    """Фаза 2: Генерация работы и сборка DOCX.

    1. Анализ прикреплённых файлов (если есть)
    2. Генерация текста через AI
    3. Сборка DOCX файла

    Returns:
        PipelineResult с generated_file.
    """
    result = PipelineResult(order_id=order.order_id, db_order_id=order_db_id)

    await update_order_status(session, order_db_id, "generating")
    await create_action_log(session, action="generate", details=f"Начата генерация: {order.work_type}", order_id=order_db_id)

    # Проверяем поддержку типа работы
    if not is_supported(order.work_type):
        result.error = f"Тип работы '{order.work_type}' пока не поддерживается"
        result.status = "error"
        await update_order_status(session, order_db_id, "error", error_message=result.error)
        return result

    # Анализ прикреплённых файлов
    methodology_summary = None
    if downloaded_files:
        file_analysis = await summarize_files(downloaded_files)
        if file_analysis:
            methodology_summary = file_analysis.get("summary", "")
            await track_api_usage(
                session, model=settings.openai_model_fast, purpose="analysis",
                input_tokens=file_analysis.get("input_tokens", 0),
                output_tokens=file_analysis.get("output_tokens", 0),
                cost_usd=file_analysis.get("cost_usd", 0),
                order_id=order_db_id,
            )

    # Генерация текста
    pages = order.pages_max or order.pages_min or 15
    gen_result = await generate_work(
        work_type=order.work_type,
        title=order.title,
        description=order.description or "",
        subject=order.subject or "",
        pages=pages,
        methodology_summary=methodology_summary,
        required_uniqueness=order.required_uniqueness,
        font_size=order.font_size,
        line_spacing=order.line_spacing,
    )

    if gen_result is None:
        result.error = "Генерация не удалась"
        result.status = "error"
        await update_order_status(session, order_db_id, "error", error_message=result.error)
        return result

    # Трекинг API генерации
    await track_api_usage(
        session, model=settings.openai_model_main, purpose="generation",
        input_tokens=gen_result.input_tokens,
        output_tokens=gen_result.output_tokens,
        cost_usd=gen_result.cost_usd,
        order_id=order_db_id,
    )

    # Сборка DOCX
    plan_dict = None
    if hasattr(gen_result, "plan") and gen_result.plan:
        plan_dict = {
            "title": gen_result.plan.title if hasattr(gen_result.plan, "title") else order.title,
            "chapters": gen_result.plan.chapters if hasattr(gen_result.plan, "chapters") else [],
        }

    docx_path = await build_docx(
        title=order.title,
        text=gen_result.text,
        work_type=order.work_type,
        subject=order.subject or "",
        font_size=order.font_size,
        line_spacing=order.line_spacing,
        plan=plan_dict,
    )

    if docx_path is None:
        result.error = "Не удалось создать DOCX файл"
        result.status = "error"
        await update_order_status(session, order_db_id, "error", error_message=result.error)
        return result

    result.generated_file = docx_path
    result.status = "generated"

    await update_order_status(
        session, order_db_id, "checking_plagiarism",
        generated_file_path=str(docx_path),
        api_cost_usd=gen_result.cost_usd,
        api_tokens_used=gen_result.total_tokens,
    )

    await create_action_log(
        session, action="generate",
        details=f"Завершено: ~{gen_result.pages_approx} стр., ${gen_result.cost_usd:.4f}",
        order_id=order_db_id,
    )

    logger.info(
        "Пайплайн завершён для заказа %s: %s, ~%d стр.",
        order.order_id, docx_path, gen_result.pages_approx,
    )

    return result
