"""AI-респондер для ведения диалога с заказчиком."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion, chat_completion_json
from src.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "chat_system.txt").read_text(encoding="utf-8")

# Слова, которые не должны появляться в ответе (проверяются как целые слова через regex)
BANNED_WORDS = [
    r"\bai\b", r"\bнейросеть\b", r"\bнейросети\b", r"\bнейросетью\b", r"\bнейросетей\b",
    r"\bchatgpt\b", r"\bgpt\b", r"\bискусственный интеллект", r"\bискусственного интеллекта\b",
    r"\bии\b", r"\bopenai\b", r"\bбот\b", r"\bботом\b", r"\bавтоматически сгенерирован",
    r"\bязыковая модель", r"\bязыковой модели",
]


@dataclass
class ChatResponse:
    """Результат генерации ответа в чат."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


async def generate_response(
    order_description: str,
    message_history: list[dict],
    new_message: str,
    order_status: str = "",
    work_type: str = "",
    subject: str = "",
    deadline: str = "",
    required_uniqueness: Optional[int] = None,
    antiplagiat_system: str = "",
    bid_price: Optional[int] = None,
    pages_min: Optional[int] = None,
    pages_max: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
    formatting_requirements: str = "",
    structure: str = "",
    special_requirements: str = "",
    files_summary: str = "",
) -> ChatResponse:
    """Сгенерировать ответ заказчику.

    Args:
        order_description: Описание заказа (тема, требования).
        message_history: История переписки [{role, content}, ...].
        new_message: Новое сообщение от заказчика.
        order_status: Текущий статус работы.
        work_type: Тип работы (Эссе, Курсовая и т.д.).
        subject: Предмет.
        deadline: Дедлайн.
        required_uniqueness: Требуемая уникальность (%).
        antiplagiat_system: Система антиплагиата.
        bid_price: Наша ставка (рубли).
        pages_min: Мин. страниц.
        pages_max: Макс. страниц.
        font_size: Размер шрифта.
        line_spacing: Межстрочный интервал.
        formatting_requirements: Требования к оформлению.
        structure: Структура/план работы.
        special_requirements: Особые требования.
        files_summary: Сводка из прикреплённых файлов.
    """
    context = _build_context(
        order_description=order_description,
        order_status=order_status,
        work_type=work_type,
        subject=subject,
        deadline=deadline,
        required_uniqueness=required_uniqueness,
        antiplagiat_system=antiplagiat_system,
        bid_price=bid_price,
        pages_min=pages_min,
        pages_max=pages_max,
        font_size=font_size,
        line_spacing=line_spacing,
        formatting_requirements=formatting_requirements,
        structure=structure,
        special_requirements=special_requirements,
        files_summary=files_summary,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст заказа:\n{context}"},
    ]

    for msg in message_history:
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    messages.append({"role": "user", "content": new_message})

    result = await chat_completion(
        messages=messages,
        model=settings.openai_model_fast,
        temperature=0.7,
        max_tokens=400,
    )

    text = result["content"].strip()
    text = _sanitize_response(text)

    logger.info(
        "Чат-ответ сгенерирован: %d токенов, $%.4f",
        result["total_tokens"], result["cost_usd"],
    )

    return ChatResponse(
        text=text,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


async def generate_greeting_message(
    work_type: str,
    subject: str,
    title: str,
    description: str,
    bid_price: int,
) -> ChatResponse:
    """Сгенерировать приветственное сообщение после постановки ставки.

    Только приветствие — без уточняющих вопросов.
    Вопросы задаются позже, когда заказчик выберет автора.
    """
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Ты автор на платформе Автор24. Ты только что поставил ставку на заказ. "
                "Напиши короткое приветственное сообщение заказчику (2-3 предложения). "
                "Поздоровайся, скажи что тема знакома и ты готов выполнить работу качественно и в срок. "
                "НЕ задавай никаких вопросов — просто приветствие и готовность. "
                "Не упоминай AI, нейросети, GPT. Пиши как реальный студент-фрилансер. "
                "Каждое сообщение должно быть уникальным — не повторяй шаблонные фразы."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Заказ: {work_type} по предмету {subject}.\n"
                f"Тема: {title}\n"
                f"Описание: {description[:500]}\n"
                f"Ставка: {bid_price}₽"
            ),
        },
    ]

    result = await chat_completion(
        messages=prompt_messages,
        model=settings.openai_model_fast,
        temperature=0.8,
        max_tokens=200,
    )

    text = _sanitize_response(result["content"].strip())

    logger.info(
        "Приветственное сообщение сгенерировано: %d токенов, $%.4f",
        result["total_tokens"], result["cost_usd"],
    )

    return ChatResponse(
        text=text,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


async def generate_clarifying_message(
    work_type: str,
    subject: str,
    title: str,
    description: str,
    required_uniqueness: Optional[int],
    antiplagiat_system: str,
    bid_price: int,
) -> Optional[ChatResponse]:
    """Сгенерировать уточняющее сообщение после принятия заказа.

    Если система антиплагиата или уникальность не указаны — спросить.
    Если всё указано — вернуть None (уточнения не нужны).
    """
    missing = []
    if not antiplagiat_system:
        missing.append("систему антиплагиата (ETXT, text.ru, Антиплагиат.ру и т.д.)")
    if not required_uniqueness:
        missing.append("требуемый процент уникальности")

    if not missing:
        return None  # Уточнения не нужны — все данные есть

    missing_str = " и ".join(missing)
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Ты автор на платформе Автор24. Заказчик только что выбрал тебя автором. "
                "Напиши короткое сообщение (2-3 предложения). "
                "Поблагодари за выбор и вежливо уточни "
                f"про {missing_str}. "
                "Не упоминай AI, нейросети, GPT. Пиши как реальный студент."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Заказ: {work_type} по предмету {subject}.\n"
                f"Тема: {title}\n"
                f"Описание: {description[:500]}\n"
                f"Ставка: {bid_price}₽"
            ),
        },
    ]

    result = await chat_completion(
        messages=prompt_messages,
        model=settings.openai_model_fast,
        temperature=0.7,
        max_tokens=200,
    )

    text = _sanitize_response(result["content"].strip())

    logger.info(
        "Уточняющее сообщение сгенерировано: %d токенов, $%.4f",
        result["total_tokens"], result["cost_usd"],
    )

    return ChatResponse(
        text=text,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


async def generate_proactive_message(
    work_type: str,
    subject: str,
    title: str,
    description: str,
    required_uniqueness: Optional[int] = None,
    antiplagiat_system: str = "",
) -> ChatResponse:
    """Сгенерировать проактивное сообщение, если заказчик молчит.

    Бот сам пишет первым: говорит что онлайн, готов начать работу,
    и при необходимости уточняет недостающие детали.
    """
    missing_parts = []
    if not antiplagiat_system:
        missing_parts.append("систему антиплагиата")
    if not required_uniqueness:
        missing_parts.append("требуемый процент уникальности")

    if missing_parts:
        clarify_hint = (
            f" Уточни {' и '.join(missing_parts)} — это нужно для работы."
        )
    else:
        clarify_hint = ""

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Ты автор на платформе Автор24. Заказчик выбрал тебя исполнителем, "
                "но ещё ничего не написал. Ты пишешь первым. "
                "Напиши короткое дружелюбное сообщение (2-3 предложения): "
                "поздоровайся, скажи что ты онлайн и уже приступаешь к работе. "
                f"{clarify_hint}"
                "Не упоминай AI, нейросети, GPT. Пиши как реальный студент-фрилансер. "
                "Не пиши длинно. Будь лаконичным."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Заказ: {work_type} по предмету {subject}.\n"
                f"Тема: {title}\n"
                f"Описание: {description[:500]}"
            ),
        },
    ]

    result = await chat_completion(
        messages=prompt_messages,
        model=settings.openai_model_fast,
        temperature=0.8,
        max_tokens=200,
    )

    text = _sanitize_response(result["content"].strip())

    logger.info(
        "Проактивное сообщение сгенерировано: %d токенов, $%.4f",
        result["total_tokens"], result["cost_usd"],
    )

    return ChatResponse(
        text=text,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
    )


async def parse_customer_answer(
    customer_text: str,
    order_context: str,
) -> dict:
    """Извлечь из ответа заказчика данные об антиплагиате и уникальности.

    Returns:
        {"antiplagiat_system": str|None, "required_uniqueness": int|None}
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Заказчик на платформе Автор24 ответил на уточняющий вопрос автора. "
                "Извлеки из ответа:\n"
                "- antiplagiat_system: система проверки антиплагиата "
                '(одно из: "ETXT", "text.ru", "Антиплагиат.ру", "Антиплагиат ВУЗ", '
                '"Руконтекст", "Страйкплагиаризм"). Если не упомянута — null.\n'
                "- required_uniqueness: требуемый процент уникальности (число 0-100). "
                "Если не упомянут — null.\n\n"
                'Верни JSON: {"antiplagiat_system": ..., "required_uniqueness": ...}\n'
                "Если заказчик написал что-то не относящееся к этим полям — верни null для обоих."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Контекст заказа:\n{order_context}\n\n"
                f"Ответ заказчика:\n{customer_text}"
            ),
        },
    ]

    result = await chat_completion_json(
        messages=messages,
        model=settings.openai_model_fast,
        temperature=0.1,
        max_tokens=100,
    )

    try:
        parsed = result["parsed"]
    except (KeyError, TypeError):
        try:
            parsed = json.loads(result.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            parsed = {}

    logger.info(
        "Парсинг ответа заказчика: %s, %d токенов, $%.4f",
        parsed, result.get("total_tokens", 0), result.get("cost_usd", 0),
    )

    return {
        "antiplagiat_system": parsed.get("antiplagiat_system"),
        "required_uniqueness": parsed.get("required_uniqueness"),
    }


async def detect_customer_approval(
    customer_text: str,
    order_context: str,
) -> dict:
    """Определить, одобрил ли заказчик работу или просит правки.

    Returns:
        {"action": "approve"|"revise"|"other", "details": str}
        - approve: заказчик доволен, можно отправить как Окончательный
        - revise: нужны правки (details = что именно)
        - other: обычное сообщение, не связано с одобрением/правками
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Ты анализируешь сообщение заказчика на платформе Автор24. "
                "Работа была отправлена как промежуточный вариант для проверки. "
                "Определи намерение заказчика:\n\n"
                '- "approve" — заказчик одобряет работу, доволен, принимает '
                '(примеры: "всё ок", "принимаю", "отлично", "подходит", '
                '"всё хорошо", "работа устраивает", "принято", "ок, принимаю")\n'
                '- "revise" — заказчик просит правки, изменения, доработки '
                '(примеры: "нужны правки", "переделай", "измени стиль", '
                '"добавь источники", "уникальность низкая", "не то")\n'
                '- "other" — обычное сообщение не об одобрении/правках '
                '(вопрос, уточнение, благодарность без одобрения и т.д.)\n\n'
                'Верни JSON: {"action": "approve"|"revise"|"other", "details": "краткое пояснение"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Контекст заказа:\n{order_context}\n\n"
                f"Сообщение заказчика:\n{customer_text}"
            ),
        },
    ]

    result = await chat_completion_json(
        messages=messages,
        model=settings.openai_model_fast,
        temperature=0.1,
        max_tokens=100,
    )

    try:
        parsed = result["parsed"]
    except (KeyError, TypeError):
        try:
            parsed = json.loads(result.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            parsed = {"action": "other", "details": ""}

    action = parsed.get("action", "other")
    if action not in ("approve", "revise", "other"):
        action = "other"

    logger.info(
        "Анализ сообщения заказчика: action=%s, details=%s, %d токенов, $%.4f",
        action, parsed.get("details", ""), result.get("total_tokens", 0), result.get("cost_usd", 0),
    )

    return {
        "action": action,
        "details": parsed.get("details", ""),
    }


async def extract_order_changes(
    assistant_message_text: str,
    current_order: dict,
) -> dict:
    """Извлечь изменения заказа из текста сообщения Ассистента через GPT-4o-mini.

    На Автор24 платформа присылает уведомления вида:
    "Заказчик изменил в заказе: тема — Новая тема"
    "Заказчик изменил в заказе: кол-во страниц — 25"

    GPT парсит текст и возвращает структурированные изменения.

    Args:
        assistant_message_text: Текст сообщения Ассистента.
        current_order: Текущие значения полей заказа в БД (для контекста).

    Returns:
        Словарь изменённых полей: {"title": "...", "pages_min": 25, ...}
        Только поля с реальными изменениями. Пустой dict если ничего не извлечено.
    """
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Ты парсишь уведомление платформы Автор24 об изменении условий заказа. "
                "Извлеки из текста какие именно поля заказа изменились и их новые значения.\n\n"
                "Возможные поля:\n"
                "- title (string): тема/название работы\n"
                "- work_type (string): тип работы (Курсовая работа, Реферат, Эссе и т.д.)\n"
                "- subject (string): предмет (Экономика, Менеджмент и т.д.)\n"
                "- description (string): описание/ТЗ заказа\n"
                "- pages_min (int): мин. количество страниц\n"
                "- pages_max (int): макс. количество страниц\n"
                "- required_uniqueness (int): требуемая уникальность в %\n"
                "- antiplagiat_system (string): система антиплагиата\n"
                "- deadline (string): дедлайн (дата в формате YYYY-MM-DD)\n"
                "- budget_rub (int): бюджет в рублях\n"
                "- font_size (int): размер шрифта\n"
                "- line_spacing (float): межстрочный интервал\n\n"
                "Верни JSON с ТОЛЬКО изменёнными полями и их НОВЫМИ значениями.\n"
                "Пример: {\"title\": \"Новая тема работы\", \"pages_max\": 30}\n"
                "Если не удалось извлечь конкретные изменения — верни пустой объект: {}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Текущий заказ:\n"
                f"- Тема: {current_order.get('title', '?')}\n"
                f"- Тип: {current_order.get('work_type', '?')}\n"
                f"- Предмет: {current_order.get('subject', '?')}\n"
                f"- Страниц: {current_order.get('pages_min', '?')}-{current_order.get('pages_max', '?')}\n"
                f"- Уникальность: {current_order.get('required_uniqueness', '?')}%\n"
                f"- Бюджет: {current_order.get('budget_rub', '?')}₽\n\n"
                f"Сообщение Ассистента:\n{assistant_message_text}"
            ),
        },
    ]

    result = await chat_completion_json(
        messages=prompt_messages,
        model=settings.openai_model_fast,
        temperature=0.0,
        max_tokens=300,
    )

    try:
        parsed = result["parsed"]
    except (KeyError, TypeError):
        try:
            parsed = json.loads(result.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            parsed = {}

    # Валидация: оставляем только известные поля с корректными типами
    valid_fields = {
        "title": str, "work_type": str, "subject": str, "description": str,
        "antiplagiat_system": str, "deadline": str,
        "pages_min": int, "pages_max": int, "required_uniqueness": int,
        "budget_rub": int, "font_size": int,
        "line_spacing": float,
    }
    changes = {}
    for field, expected_type in valid_fields.items():
        if field in parsed and parsed[field] is not None:
            try:
                changes[field] = expected_type(parsed[field])
            except (ValueError, TypeError):
                pass  # Пропускаем невалидные значения

    logger.info(
        "GPT извлёк изменения из сообщения Ассистента: %s, %d токенов, $%.4f",
        changes or "(пусто)",
        result.get("total_tokens", 0), result.get("cost_usd", 0),
    )

    return changes


def _build_context(
    order_description: str,
    order_status: str,
    work_type: str,
    subject: str,
    deadline: str,
    required_uniqueness: Optional[int],
    antiplagiat_system: str,
    bid_price: Optional[int] = None,
    pages_min: Optional[int] = None,
    pages_max: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
    formatting_requirements: str = "",
    structure: str = "",
    special_requirements: str = "",
    files_summary: str = "",
) -> str:
    """Построить контекст заказа для промпта."""
    parts = []

    if work_type:
        parts.append(f"Тип работы: {work_type}")
    if subject:
        parts.append(f"Предмет: {subject}")
    if deadline:
        parts.append(f"Дедлайн: {deadline}")
    if order_status:
        parts.append(f"Статус: {order_status}")
    if bid_price:
        parts.append(f"Наша ставка: {bid_price}₽")
    if pages_min or pages_max:
        if pages_min == pages_max:
            parts.append(f"Объём: {pages_min} стр.")
        else:
            parts.append(f"Объём: {pages_min or '?'}-{pages_max or '?'} стр.")
    if font_size and font_size != 14:
        parts.append(f"Шрифт: {font_size} пт")
    if line_spacing and line_spacing != 1.5:
        parts.append(f"Интервал: {line_spacing}")
    if required_uniqueness:
        parts.append(f"Требуемая уникальность: {required_uniqueness}%")
    if antiplagiat_system:
        parts.append(f"Система антиплагиата: {antiplagiat_system}")
    if formatting_requirements:
        parts.append(f"Оформление: {formatting_requirements}")
    if structure:
        parts.append(f"Структура: {structure}")
    if special_requirements:
        parts.append(f"Особые требования: {special_requirements}")
    if files_summary:
        parts.append(f"\nДанные из прикреплённых файлов:\n{files_summary}")
    if order_description:
        parts.append(f"\nОписание заказа:\n{order_description}")

    return "\n".join(parts)


def _sanitize_response(text: str) -> str:
    """Проверить ответ на запрещённые слова (целые слова через regex)."""
    import re
    lower = text.lower()
    for pattern in BANNED_WORDS:
        if re.search(pattern, lower):
            logger.warning("Обнаружено запрещённое слово в ответе: '%s'", pattern)
    return text
