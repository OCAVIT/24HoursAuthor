"""Обёртка над OpenAI API с трекингом использования токенов."""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from src.config import settings

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

# Стоимость за 1M токенов (USD)
MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Рассчитать стоимость вызова API в USD."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["gpt-4o"])
    cost = (input_tokens / 1_000_000) * pricing["input"] + \
           (output_tokens / 1_000_000) * pricing["output"]
    return round(cost, 6)


async def chat_completion(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: Optional[dict] = None,
) -> dict:
    """Вызвать OpenAI Chat Completion и вернуть результат с метаданными.

    Returns:
        {
            "content": str,
            "model": str,
            "input_tokens": int,
            "output_tokens": int,
            "total_tokens": int,
            "cost_usd": float,
        }
    """
    model = model or settings.openai_model_main

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    response = await client.chat.completions.create(**kwargs)

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = calculate_cost(model, input_tokens, output_tokens)

    content = response.choices[0].message.content or ""

    logger.info(
        "OpenAI %s: %d in / %d out tokens, $%.4f",
        model, input_tokens, output_tokens, cost,
    )

    return {
        "content": content,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost,
    }


async def chat_completion_json(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> dict:
    """Вызвать OpenAI и получить JSON-ответ.

    Returns:
        {
            "data": dict/list (parsed JSON),
            "model": str,
            "input_tokens": int,
            "output_tokens": int,
            "total_tokens": int,
            "cost_usd": float,
        }
    """
    result = await chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(result["content"])
    except json.JSONDecodeError:
        logger.error("Не удалось распарсить JSON из ответа OpenAI: %s", result["content"][:200])
        data = {}

    return {
        "data": data,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "total_tokens": result["total_tokens"],
        "cost_usd": result["cost_usd"],
    }
