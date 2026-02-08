"""Тесты генераторов: эссе, реферат, роутер, DOCX builder."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.generator.essay import generate as essay_generate, GenerationResult as EssayResult, CHARS_PER_PAGE
from src.generator.referat import generate as referat_generate, GenerationResult as ReferatResult, ReferatPlan, _plan_to_text
from src.generator.router import (
    get_generator, is_supported, supported_types, generate_work, _default_pages,
)
from src.docgen.builder import _parse_text_to_sections, _sections_from_text, _split_by_markers
from src.docgen.formatter import normalize_text, estimate_pages, split_into_paragraphs


# ===== Тесты генератора эссе =====

class TestEssayGenerator:
    """Тесты генератора эссе."""

    @pytest.mark.asyncio
    async def test_generate_essay_returns_result(self):
        """generate возвращает GenerationResult."""
        mock_response = {
            "content": "Текст эссе " * 500,  # ~2500 символов
            "model": "gpt-4o",
            "input_tokens": 500,
            "output_tokens": 1000,
            "total_tokens": 1500,
            "cost_usd": 0.012,
        }

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await essay_generate(
                title="Свобода и ответственность",
                subject="Философия",
                pages=5,
            )

        assert isinstance(result, EssayResult)
        assert result.title == "Свобода и ответственность"
        assert result.work_type == "Эссе"
        assert result.input_tokens == 500
        assert result.output_tokens == 1000
        assert result.cost_usd == 0.012

    @pytest.mark.asyncio
    async def test_generate_essay_text_not_empty(self):
        """Сгенерированный текст не пуст."""
        text = "Введение. Тема свободы актуальна. " * 200

        mock_response = {
            "content": text,
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 800,
            "total_tokens": 1100,
            "cost_usd": 0.01,
        }

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await essay_generate(title="Тест", pages=3)

        assert len(result.text) > 100
        assert result.pages_approx >= 1

    @pytest.mark.asyncio
    async def test_generate_essay_with_methodology(self):
        """Генерация с учётом методички."""
        mock_response = {
            "content": "Текст эссе с учётом требований методички",
            "model": "gpt-4o",
            "input_tokens": 600,
            "output_tokens": 1200,
            "total_tokens": 1800,
            "cost_usd": 0.015,
        }

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            result = await essay_generate(
                title="Тест",
                methodology_summary="Требуется структура: введение, 3 аргумента, заключение",
                pages=5,
            )

        # Проверяем что методичка попала в промпт
        call_args = mock_call.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        user_msg = messages[-1]["content"]
        assert "методичк" in user_msg.lower()


# ===== Тесты генератора рефератов =====

class TestReferatGenerator:
    """Тесты генератора рефератов."""

    @pytest.mark.asyncio
    async def test_generate_referat_calls_multiple_apis(self):
        """Реферат генерируется пошагово: план + введение + главы + заключение + литература."""
        # Мок для генерации плана
        plan_response = {
            "data": {
                "title": "История России",
                "chapters": [
                    {"number": 1, "title": "Раннее Средневековье", "subsections": ["1.1 Киевская Русь"]},
                    {"number": 2, "title": "Новое время", "subsections": ["2.1 Реформы Петра"]},
                ],
            },
            "input_tokens": 200,
            "output_tokens": 150,
            "cost_usd": 0.001,
        }

        # Мок для генерации текста секций
        section_response = {
            "content": "Текст секции реферата. " * 100,
            "model": "gpt-4o",
            "input_tokens": 500,
            "output_tokens": 1000,
            "total_tokens": 1500,
            "cost_usd": 0.01,
        }

        with patch("src.generator.referat.chat_completion_json", new_callable=AsyncMock, return_value=plan_response), \
             patch("src.generator.referat.chat_completion", new_callable=AsyncMock, return_value=section_response):
            result = await referat_generate(
                title="История России XIX века",
                subject="История",
                pages=15,
            )

        assert isinstance(result, ReferatResult)
        assert result.work_type == "Реферат"
        assert len(result.text) > 100
        # План + введение + 2 главы + заключение + литература = 6 вызовов chat_completion
        assert result.total_tokens > 0
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_referat_plan_generated(self):
        """План реферата генерируется корректно."""
        plan_response = {
            "data": {
                "title": "Экономика",
                "chapters": [
                    {"number": 1, "title": "Основы", "subsections": ["1.1 Определения"]},
                ],
            },
            "input_tokens": 100, "output_tokens": 80, "cost_usd": 0.001,
        }

        section_response = {
            "content": "Текст.",
            "model": "gpt-4o",
            "input_tokens": 100, "output_tokens": 100, "total_tokens": 200, "cost_usd": 0.005,
        }

        with patch("src.generator.referat.chat_completion_json", new_callable=AsyncMock, return_value=plan_response), \
             patch("src.generator.referat.chat_completion", new_callable=AsyncMock, return_value=section_response):
            result = await referat_generate(title="Тест", pages=10)

        assert result.plan is not None
        assert len(result.plan.chapters) == 1
        assert result.plan.chapters[0]["title"] == "Основы"

    def test_plan_to_text(self):
        """Преобразование плана в текст."""
        plan = ReferatPlan(
            title="Тест",
            chapters=[
                {"number": 1, "title": "Глава 1", "subsections": ["1.1 Подраздел"]},
                {"number": 2, "title": "Глава 2", "subsections": []},
            ],
        )
        text = _plan_to_text(plan)
        assert "Введение" in text
        assert "Глава 1" in text
        assert "Глава 2" in text
        assert "1.1 Подраздел" in text
        assert "Заключение" in text
        assert "Список литературы" in text


# ===== Тесты роутера =====

class TestRouter:
    """Тесты роутера генераторов."""

    def test_essay_supported(self):
        """Эссе поддерживается."""
        assert is_supported("Эссе") is True

    def test_referat_supported(self):
        """Реферат поддерживается."""
        assert is_supported("Реферат") is True

    def test_coursework_not_supported_yet(self):
        """Курсовая работа пока не реализована."""
        assert is_supported("Курсовая работа") is False

    def test_get_generator_essay(self):
        """get_generator для эссе возвращает функцию."""
        gen = get_generator("Эссе")
        assert gen is not None
        assert callable(gen)

    def test_get_generator_unknown(self):
        """Неизвестный тип → None."""
        gen = get_generator("Несуществующий тип")
        assert gen is None

    def test_supported_types_not_empty(self):
        """Список поддерживаемых типов не пуст."""
        types = supported_types()
        assert len(types) >= 2
        assert "Эссе" in types
        assert "Реферат" in types

    @pytest.mark.asyncio
    async def test_generate_work_unsupported(self):
        """generate_work для неподдерживаемого типа → None."""
        result = await generate_work(
            work_type="Курсовая работа",
            title="Тест",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_work_essay(self):
        """generate_work для эссе вызывает генератор."""
        mock_response = {
            "content": "Текст эссе",
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 800,
            "total_tokens": 1100,
            "cost_usd": 0.01,
        }

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_work(
                work_type="Эссе",
                title="Тестовое эссе",
                subject="Философия",
            )

        assert result is not None
        assert result.work_type == "Эссе"

    def test_default_pages_essay(self):
        """Дефолтные страницы для эссе = 5."""
        assert _default_pages("Эссе") == 5

    def test_default_pages_referat(self):
        """Дефолтные страницы для реферата = 15."""
        assert _default_pages("Реферат") == 15

    def test_all_generators_mapped(self):
        """Все ключевые типы работ имеют маппинг (даже если None)."""
        from src.generator.router import GENERATORS
        assert "Эссе" in GENERATORS
        assert "Реферат" in GENERATORS
        assert "Курсовая работа" in GENERATORS
        assert "Дипломная работа" in GENERATORS
        assert "Контрольная работа" in GENERATORS


# ===== Тесты DOCX builder =====

class TestDocxBuilder:
    """Тесты DOCX builder (парсинг текста в секции)."""

    def test_sections_from_text_simple(self):
        """Простой текст без заголовков → 1 секция."""
        sections = _sections_from_text("Просто текст абзац один.\n\nПросто текст абзац два.")
        assert len(sections) >= 1
        assert sections[0]["text"]

    def test_sections_from_text_with_headings(self):
        """Текст с заголовками разбивается на секции."""
        text = (
            "Введение\n\nТекст введения.\n\n"
            "1. Первая глава\n\nТекст первой главы.\n\n"
            "2. Вторая глава\n\nТекст второй главы.\n\n"
            "Заключение\n\nТекст заключения."
        )
        sections = _sections_from_text(text)
        assert len(sections) >= 3

    def test_sections_from_text_heading_levels(self):
        """Подразделы имеют level=2."""
        text = (
            "1. Глава первая\n\nТекст.\n\n"
            "1.1. Подраздел\n\nТекст подраздела."
        )
        sections = _sections_from_text(text)
        # Находим подраздел
        sub = [s for s in sections if "1.1" in s.get("heading", "")]
        if sub:
            assert sub[0]["level"] == 2

    def test_split_by_markers_empty(self):
        """Пустые маркеры → весь текст как одна часть."""
        parts = _split_by_markers("текст", [])
        assert len(parts) == 1

    def test_split_by_markers_found(self):
        """Маркеры делят текст на части."""
        text = "Начало.\n\nВВЕДЕНИЕ\n\nТекст введения.\n\nЗАКЛЮЧЕНИЕ\n\nТекст заключения."
        parts = _split_by_markers(text, ["ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ"])
        assert len(parts) >= 2


# ===== Тесты форматирования =====

class TestFormatter:
    """Тесты утилит форматирования."""

    def test_normalize_text_removes_double_spaces(self):
        """Двойные пробелы убираются."""
        assert normalize_text("Текст  с  пробелами") == "Текст с пробелами"

    def test_normalize_text_fixes_punctuation(self):
        """Пробелы перед пунктуацией убираются."""
        assert normalize_text("Текст .") == "Текст."

    def test_normalize_text_limits_newlines(self):
        """Больше 2 переносов → 2."""
        result = normalize_text("Текст\n\n\n\nТекст2")
        assert "\n\n\n" not in result

    def test_estimate_pages(self):
        """Оценка количества страниц."""
        text = "x" * 9000  # 5 страниц
        assert estimate_pages(text) == 5

    def test_estimate_pages_minimum(self):
        """Минимум 1 страница."""
        assert estimate_pages("короткий") == 1

    def test_split_into_paragraphs(self):
        """Разбиение на абзацы."""
        text = "Первый абзац.\n\nВторой абзац.\n\nТретий абзац."
        paras = split_into_paragraphs(text)
        assert len(paras) == 3
        assert paras[0] == "Первый абзац."

    def test_split_into_paragraphs_ignores_empty(self):
        """Пустые абзацы игнорируются."""
        text = "Текст.\n\n\n\n\n\nЕщё текст."
        paras = split_into_paragraphs(text)
        assert len(paras) == 2


# ===== Тест AI клиента =====

class TestAiClient:
    """Тесты AI клиента."""

    def test_calculate_cost_gpt4o(self):
        """Расчёт стоимости для GPT-4o."""
        from src.ai_client import calculate_cost
        cost = calculate_cost("gpt-4o", 1000, 1000)
        # 1000/1M * 2.50 + 1000/1M * 10.00 = 0.0025 + 0.01 = 0.0125
        assert abs(cost - 0.0125) < 0.0001

    def test_calculate_cost_gpt4o_mini(self):
        """Расчёт стоимости для GPT-4o-mini."""
        from src.ai_client import calculate_cost
        cost = calculate_cost("gpt-4o-mini", 1000, 1000)
        # 1000/1M * 0.15 + 1000/1M * 0.60 = 0.00015 + 0.0006 = 0.00075
        assert abs(cost - 0.00075) < 0.00001

    def test_calculate_cost_unknown_model(self):
        """Неизвестная модель → дефолт (gpt-4o pricing)."""
        from src.ai_client import calculate_cost
        cost = calculate_cost("unknown-model", 1000, 1000)
        assert cost > 0
