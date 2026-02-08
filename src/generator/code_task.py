"""Генератор решений задач по программированию с AI-циклом исправления."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ai_client import chat_completion
from src.config import settings
from src.sandbox.executor import execute_code, ExecutionResult
from src.sandbox.languages import detect_language, get_language

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "code_task_system.txt").read_text(encoding="utf-8")

MAX_FIX_ATTEMPTS = 5


@dataclass
class CodeGenerationResult:
    """Результат генерации кода."""
    text: str  # финальный код
    title: str
    work_type: str = "Задача по программированию"
    pages_approx: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    language: str = "python"
    execution_result: Optional[ExecutionResult] = None
    attempts: int = 1
    fix_history: list[str] = field(default_factory=list)


# Алиас для совместимости с роутером
GenerationResult = CodeGenerationResult


async def generate(
    title: str,
    description: str = "",
    subject: str = "",
    pages: int = 1,
    methodology_summary: Optional[str] = None,
    required_uniqueness: Optional[int] = None,
    font_size: int = 14,
    line_spacing: float = 1.5,
) -> GenerationResult:
    """Сгенерировать код с AI-циклом исправления.

    Цикл: GPT-4o генерирует код → sandbox запускает →
    если ошибка → stderr обратно в GPT → повтор до 5 раз.

    Args:
        title: Название задачи.
        description: Условие задачи / ТЗ.
        subject: Предмет или язык программирования.
        pages: Не используется для кода.
        methodology_summary: Дополнительные требования.
        required_uniqueness: Не используется для кода.
        font_size: Не используется для кода.
        line_spacing: Не используется для кода.
    """
    # Определяем язык
    hint = f"{title} {description} {subject}"
    language = detect_language("", hint)

    # Формируем промпт
    user_prompt = _build_prompt(
        title=title,
        description=description,
        subject=subject,
        language=language,
        methodology_summary=methodology_summary,
    )

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    fix_history: list[str] = []
    previous_error: Optional[str] = None
    code = ""
    exec_result: Optional[ExecutionResult] = None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        logger.info("Генерация кода, попытка %d/%d (язык: %s)", attempt, MAX_FIX_ATTEMPTS, language)

        # Если предыдущая попытка завершилась ошибкой — добавляем в контекст
        if previous_error and attempt > 1:
            messages.append({
                "role": "assistant",
                "content": code,
            })
            messages.append({
                "role": "user",
                "content": (
                    f"Код выдал ошибку при запуске:\n\n{previous_error}\n\n"
                    "Исправь код. Верни ТОЛЬКО исправленный код, без пояснений."
                ),
            })

        # Генерация кода через GPT-4o
        result = await chat_completion(
            messages=messages,
            model=settings.openai_model_main,
            temperature=0.3,
            max_tokens=4096,
        )

        code = _clean_code(result["content"])
        total_input_tokens += result["input_tokens"]
        total_output_tokens += result["output_tokens"]
        total_cost += result["cost_usd"]

        # Запуск в песочнице
        exec_result = await execute_code(
            code=code,
            language=language,
            timeout=30,
        )

        if exec_result.success:
            logger.info(
                "Код выполнен успешно с попытки %d/%d, %d токенов, $%.4f",
                attempt, MAX_FIX_ATTEMPTS, total_input_tokens + total_output_tokens, total_cost,
            )
            break

        # Ошибка — запоминаем для следующей попытки
        error_msg = exec_result.stderr or f"Exit code: {exec_result.exit_code}"
        if exec_result.timed_out:
            error_msg = "Превышен таймаут выполнения (30 секунд). Оптимизируй алгоритм."

        fix_history.append(f"Попытка {attempt}: {error_msg[:500]}")
        previous_error = error_msg
        logger.warning("Попытка %d: ошибка — %s", attempt, error_msg[:200])

    # Формируем результат
    # Текст = код + вывод (для отправки заказчику)
    output_text = _format_output(code, language, exec_result)

    return GenerationResult(
        text=output_text,
        title=title,
        work_type="Задача по программированию",
        pages_approx=1,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        total_tokens=total_input_tokens + total_output_tokens,
        cost_usd=total_cost,
        language=language,
        execution_result=exec_result,
        attempts=attempt,
        fix_history=fix_history,
    )


def _build_prompt(
    title: str,
    description: str,
    subject: str,
    language: str,
    methodology_summary: Optional[str],
) -> str:
    """Построить промпт для генерации кода."""
    lang_config = get_language(language)
    lang_name = lang_config.name if lang_config else language

    parts = [
        f"Задача: {title}",
        f"Язык программирования: {lang_name}",
    ]

    if subject:
        parts.append(f"Предмет: {subject}")

    if description:
        parts.append(f"\nУсловие задачи:\n{description}")

    if methodology_summary:
        parts.append(f"\nДополнительные требования:\n{methodology_summary}")

    parts.append(f"\nНапиши решение на {lang_name}. Только код, без пояснений.")

    return "\n".join(parts)


def _clean_code(raw: str) -> str:
    """Очистить код от markdown-обёрток."""
    code = raw.strip()

    # Убираем ```python ... ``` и подобные обёртки
    if code.startswith("```"):
        lines = code.split("\n")
        # Убираем первую строку (```python)
        lines = lines[1:]
        # Убираем последнюю строку (```) если есть
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)

    return code.strip()


def _format_output(
    code: str,
    language: str,
    exec_result: Optional[ExecutionResult],
) -> str:
    """Форматировать итоговый текст для отправки заказчику."""
    lang_config = get_language(language)
    lang_name = lang_config.name if lang_config else language

    parts = [
        f"=== Решение на {lang_name} ===\n",
        code,
    ]

    if exec_result and exec_result.success and exec_result.stdout:
        parts.append(f"\n\n=== Результат выполнения ===\n{exec_result.stdout}")
    elif exec_result and not exec_result.success:
        parts.append("\n\n=== Внимание: код содержит ошибки ===")
        if exec_result.stderr:
            parts.append(exec_result.stderr[:500])

    return "\n".join(parts)
