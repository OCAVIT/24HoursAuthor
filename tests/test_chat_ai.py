"""Тесты AI-чата с заказчиком: генерация ответов, фильтрация, контекст."""

import pytest
from unittest.mock import AsyncMock, patch

from src.chat_ai.responder import (
    generate_response,
    ChatResponse,
    _build_context,
    _sanitize_response,
    BANNED_WORDS,
)


# ===== Тесты генерации ответа =====

class TestResponseGeneration:
    """Тесты генерации ответа заказчику."""

    @pytest.mark.asyncio
    async def test_response_generation(self):
        """generate_response() возвращает ChatResponse с текстом."""
        mock_response = {
            "content": "Да, тема знакома, смогу сделать в срок.",
            "model": "gpt-4o-mini",
            "input_tokens": 150,
            "output_tokens": 20,
            "total_tokens": 170,
            "cost_usd": 0.00003,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_response(
                order_description="Курсовая по экономике, 30 страниц",
                message_history=[],
                new_message="Сможете сделать?",
            )

        assert isinstance(result, ChatResponse)
        assert result.text == "Да, тема знакома, смогу сделать в срок."
        assert result.input_tokens == 150
        assert result.output_tokens == 20
        assert result.total_tokens == 170
        assert result.cost_usd == 0.00003

    @pytest.mark.asyncio
    async def test_response_with_history(self):
        """Ответ генерируется с учётом истории переписки."""
        mock_response = {
            "content": "Планирую закончить завтра к вечеру.",
            "model": "gpt-4o-mini",
            "input_tokens": 250,
            "output_tokens": 15,
            "total_tokens": 265,
            "cost_usd": 0.00005,
        }

        history = [
            {"role": "user", "content": "Сможете сделать?"},
            {"role": "assistant", "content": "Да, тема знакома."},
        ]

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_response(
                order_description="Реферат по истории",
                message_history=history,
                new_message="Когда будет готово?",
            )

        assert result.text == "Планирую закончить завтра к вечеру."

    @pytest.mark.asyncio
    async def test_tokens_tracked(self):
        """Токены и стоимость отслеживаются корректно."""
        mock_response = {
            "content": "Конечно, скиньте что исправить.",
            "model": "gpt-4o-mini",
            "input_tokens": 300,
            "output_tokens": 12,
            "total_tokens": 312,
            "cost_usd": 0.00006,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_response(
                order_description="Эссе по философии",
                message_history=[],
                new_message="Нужны правки",
            )

        assert result.input_tokens == 300
        assert result.output_tokens == 12
        assert result.total_tokens == 312
        assert result.cost_usd == 0.00006


# ===== Тесты на запрещённые слова =====

class TestNoAIMention:
    """Ответ не должен содержать упоминания AI/нейросетей."""

    @pytest.mark.asyncio
    async def test_no_ai_mention(self):
        """Ответ не содержит слов AI, нейросеть, ChatGPT, GPT, искусственный интеллект."""
        mock_response = {
            "content": "Да, смогу сделать. Тема мне знакома по учёбе.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 15,
            "total_tokens": 115,
            "cost_usd": 0.00002,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_response(
                order_description="Курсовая работа",
                message_history=[],
                new_message="Как вы будете делать?",
            )

        import re
        lower = result.text.lower()
        for pattern in BANNED_WORDS:
            assert not re.search(pattern, lower), f"Ответ содержит запрещённое слово: '{pattern}'"

    @pytest.mark.asyncio
    async def test_banned_words_list_complete(self):
        """Список запрещённых слов содержит все ключевые варианты."""
        joined = " ".join(BANNED_WORDS)
        assert "ai" in joined
        assert "нейросеть" in joined
        assert "chatgpt" in joined
        assert "gpt" in joined
        assert "искусственный интеллект" in joined


# ===== Тесты длины ответа =====

class TestResponseLength:
    """Ответ должен быть коротким — не более 3 предложений."""

    @pytest.mark.asyncio
    async def test_response_length_short(self):
        """Ответ содержит не более 3 предложений."""
        mock_response = {
            "content": "Да, тема знакома. Смогу сделать в срок. Начну сегодня.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 15,
            "total_tokens": 115,
            "cost_usd": 0.00002,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_response(
                order_description="Эссе",
                message_history=[],
                new_message="Сможете?",
            )

        sentences = [s.strip() for s in result.text.split(".") if s.strip()]
        assert len(sentences) <= 3, f"Ответ содержит {len(sentences)} предложений: {result.text}"

    @pytest.mark.asyncio
    async def test_max_tokens_limited(self):
        """Вызов OpenAI ограничивает max_tokens для краткости."""
        mock_response = {
            "content": "Ок.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "cost_usd": 0.00001,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            await generate_response(
                order_description="Тест",
                message_history=[],
                new_message="Привет",
            )

        _, kwargs = mock_call.call_args
        assert kwargs["max_tokens"] <= 400


# ===== Тесты передачи контекста =====

class TestContextIncluded:
    """В промпт передаётся описание заказа и контекст."""

    @pytest.mark.asyncio
    async def test_context_included(self):
        """В промпт передаётся описание заказа."""
        mock_response = {
            "content": "Ок.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "cost_usd": 0.00001,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            await generate_response(
                order_description="Курсовая по макроэкономике, 30 страниц, ГОСТ",
                message_history=[],
                new_message="Привет",
                work_type="Курсовая работа",
                subject="Макроэкономика",
            )

        args, kwargs = mock_call.call_args
        messages = kwargs["messages"]

        # Системный промпт присутствует
        assert messages[0]["role"] == "system"

        # Контекст заказа присутствует
        context_msg = messages[1]["content"]
        assert "Курсовая по макроэкономике" in context_msg
        assert "Курсовая работа" in context_msg
        assert "Макроэкономика" in context_msg

    @pytest.mark.asyncio
    async def test_order_status_in_context(self):
        """Статус заказа передаётся в контекст."""
        mock_response = {
            "content": "Работа почти готова.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "cost_usd": 0.00001,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            await generate_response(
                order_description="Эссе по философии",
                message_history=[],
                new_message="Как дела с работой?",
                order_status="generating",
            )

        messages = mock_call.call_args.kwargs["messages"]
        context_msg = messages[1]["content"]
        assert "generating" in context_msg

    @pytest.mark.asyncio
    async def test_uses_fast_model(self):
        """Для чата используется быстрая модель (gpt-4o-mini)."""
        mock_response = {
            "content": "Ок.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "cost_usd": 0.00001,
        }

        with patch("src.chat_ai.responder.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_call, \
             patch("src.chat_ai.responder.settings") as mock_settings:
            mock_settings.openai_model_fast = "gpt-4o-mini"
            await generate_response(
                order_description="Тест",
                message_history=[],
                new_message="Привет",
            )

        kwargs = mock_call.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"


# ===== Тесты вспомогательных функций =====

class TestHelpers:
    """Тесты вспомогательных функций."""

    def test_build_context_full(self):
        """_build_context() собирает все поля."""
        ctx = _build_context(
            order_description="Описание заказа",
            order_status="generating",
            work_type="Курсовая работа",
            subject="Экономика",
            deadline="2026-02-15",
            required_uniqueness=70,
            antiplagiat_system="ETXT",
        )

        assert "Курсовая работа" in ctx
        assert "Экономика" in ctx
        assert "2026-02-15" in ctx
        assert "generating" in ctx
        assert "70%" in ctx
        assert "ETXT" in ctx
        assert "Описание заказа" in ctx

    def test_build_context_minimal(self):
        """_build_context() работает с минимальными данными."""
        ctx = _build_context(
            order_description="Тест",
            order_status="",
            work_type="",
            subject="",
            deadline="",
            required_uniqueness=None,
            antiplagiat_system="",
        )

        assert "Тест" in ctx

    def test_sanitize_response_clean(self):
        """_sanitize_response() пропускает чистый текст."""
        text = "Да, тема знакома, смогу сделать."
        assert _sanitize_response(text) == text

    def test_sanitize_response_returns_text(self):
        """_sanitize_response() всегда возвращает текст (даже с предупреждением)."""
        text = "Я использую нейросеть для работы."
        result = _sanitize_response(text)
        assert result == text  # текст возвращается, но логируется предупреждение
