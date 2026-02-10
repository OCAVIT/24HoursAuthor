"""Извлечение недостающих полей заказа из описания и файлов через GPT-4o-mini."""

import logging
from dataclasses import dataclass
from typing import Optional

from src.ai_client import chat_completion_json
from src.config import settings
from src.scraper.order_detail import OrderDetail

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
Ты анализируешь текст заказа на написание работы. Извлеки из текста следующие параметры.
Верни JSON с ТОЛЬКО теми полями, которые ЯВНО указаны в тексте. Не угадывай.

Возможные поля:
- "pages_min": int — минимальное количество страниц
- "pages_max": int — максимальное количество страниц
- "required_uniqueness": int — процент оригинальности (например 60, 70, 80)
- "antiplagiat_system": string — система антиплагиата (ETXT, Антиплагиат.ру, text.ru)
- "font_size": int — размер шрифта (12, 14, и т.д.)
- "line_spacing": float — межстрочный интервал (1.0, 1.5, 2.0)
- "formatting_requirements": string — требования к оформлению (поля, отступы, нумерация и т.д.)
- "structure": string — структура/план работы (главы, разделы)
- "special_requirements": string — особые требования (методички, примечания, ограничения)

Пример ответа:
{"pages_min": 20, "pages_max": 25, "required_uniqueness": 70, "formatting_requirements": "Шрифт Times New Roman 14, интервал 1.5, поля 2см"}

Если параметр НЕ упоминается в тексте — НЕ включай его в ответ.
"""


@dataclass
class ExtractionResult:
    """Результат извлечения полей."""
    order: OrderDetail
    fields_extracted: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


async def extract_missing_fields(
    order: OrderDetail,
    files_text: str = "",
) -> ExtractionResult:
    """Заполнить пустые поля OrderDetail, извлекая из описания и файлов через GPT-4o-mini.

    Приоритет: HTML-парсинг (уже заполнено) > описание > файлы.
    НИКОГДА не перезаписывает уже заполненные поля (кроме дефолтных значений).

    Returns:
        ExtractionResult с обновлённым OrderDetail и метаданными.
    """
    # Определяем какие поля пустые / имеют дефолтные значения
    missing = []
    if order.pages_min is None and order.pages_max is None:
        missing.append("pages_min/pages_max")
    if order.required_uniqueness is None:
        missing.append("required_uniqueness")
    if not order.antiplagiat_system:
        missing.append("antiplagiat_system")
    if order.font_size == 14:  # дефолт — возможно есть реальное значение в тексте
        missing.append("font_size")
    if order.line_spacing == 1.5:  # дефолт
        missing.append("line_spacing")
    if not order.formatting_requirements:
        missing.append("formatting_requirements")
    if not order.structure:
        missing.append("structure")
    if not order.special_requirements:
        missing.append("special_requirements")

    # Если всё заполнено — нечего извлекать
    if not missing:
        logger.info("Заказ %s: все поля уже заполнены, извлечение не требуется", order.order_id)
        return ExtractionResult(order=order, fields_extracted=[])

    # Формируем текст для анализа
    text_parts = []
    if order.description:
        text_parts.append(f"ОПИСАНИЕ ЗАКАЗА:\n{order.description}")
    if files_text:
        # Ограничиваем текст файлов
        truncated = files_text[:8000] if len(files_text) > 8000 else files_text
        text_parts.append(f"СОДЕРЖИМОЕ ПРИКРЕПЛЁННЫХ ФАЙЛОВ:\n{truncated}")

    combined_text = "\n\n".join(text_parts)
    if not combined_text.strip():
        logger.info("Заказ %s: нет текста для извлечения полей", order.order_id)
        return ExtractionResult(order=order, fields_extracted=[])

    # Вызываем GPT-4o-mini
    try:
        result = await chat_completion_json(
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": combined_text},
            ],
            model=settings.openai_model_fast,
            temperature=0.1,
            max_tokens=512,
        )
    except Exception as e:
        logger.error("Ошибка извлечения полей для заказа %s: %s", order.order_id, e)
        return ExtractionResult(order=order, fields_extracted=[])

    data = result.get("data", {})
    if not data:
        return ExtractionResult(
            order=order,
            fields_extracted=[],
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            cost_usd=result.get("cost_usd", 0),
        )

    # Заполняем ТОЛЬКО пустые поля
    fields_extracted = []

    # pages_min / pages_max
    if order.pages_min is None and "pages_min" in data:
        val = _safe_int(data["pages_min"])
        if val:
            order.pages_min = val
            fields_extracted.append("pages_min")
    if order.pages_max is None and "pages_max" in data:
        val = _safe_int(data["pages_max"])
        if val:
            order.pages_max = val
            fields_extracted.append("pages_max")

    # required_uniqueness
    if order.required_uniqueness is None and "required_uniqueness" in data:
        val = _safe_int(data["required_uniqueness"])
        if val and 0 < val <= 100:
            order.required_uniqueness = val
            fields_extracted.append("required_uniqueness")

    # antiplagiat_system
    if not order.antiplagiat_system and "antiplagiat_system" in data:
        val = str(data["antiplagiat_system"]).strip()
        if val:
            order.antiplagiat_system = val
            fields_extracted.append("antiplagiat_system")

    # font_size (перезаписываем дефолт 14 только если другое значение)
    if order.font_size == 14 and "font_size" in data:
        val = _safe_int(data["font_size"])
        if val and val != 14 and 8 <= val <= 20:
            order.font_size = val
            fields_extracted.append("font_size")

    # line_spacing (перезаписываем дефолт 1.5 только если другое значение)
    if order.line_spacing == 1.5 and "line_spacing" in data:
        val = _safe_float(data["line_spacing"])
        if val and val != 1.5 and 0.5 <= val <= 3.0:
            order.line_spacing = val
            fields_extracted.append("line_spacing")

    # formatting_requirements
    if not order.formatting_requirements and "formatting_requirements" in data:
        val = str(data["formatting_requirements"]).strip()
        if val:
            order.formatting_requirements = val
            fields_extracted.append("formatting_requirements")

    # structure
    if not order.structure and "structure" in data:
        val = str(data["structure"]).strip()
        if val:
            order.structure = val
            fields_extracted.append("structure")

    # special_requirements
    if not order.special_requirements and "special_requirements" in data:
        val = str(data["special_requirements"]).strip()
        if val:
            order.special_requirements = val
            fields_extracted.append("special_requirements")

    if fields_extracted:
        order.extracted_from_files = True
        logger.info(
            "Заказ %s: извлечены поля из текста: %s",
            order.order_id, ", ".join(fields_extracted),
        )

    return ExtractionResult(
        order=order,
        fields_extracted=fields_extracted,
        input_tokens=result.get("input_tokens", 0),
        output_tokens=result.get("output_tokens", 0),
        cost_usd=result.get("cost_usd", 0),
    )


def _safe_int(value) -> Optional[int]:
    """Безопасно преобразовать значение в int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> Optional[float]:
    """Безопасно преобразовать значение в float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
