"""Тесты генераторов: все типы работ, роутер, DOCX builder."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.generator.essay import generate as essay_generate, GenerationResult as EssayResult, CHARS_PER_PAGE
from src.generator.referat import generate as referat_generate, GenerationResult as ReferatResult, ReferatPlan, _plan_to_text
from src.generator.coursework import generate as coursework_generate, GenerationResult as CourseworkResult
from src.generator.diploma import generate as diploma_generate, GenerationResult as DiplomaResult
from src.generator.homework import generate as homework_generate, GenerationResult as HomeworkResult
from src.generator.presentation import generate as presentation_generate, GenerationResult as PresentationResult
from src.generator.translation import generate as translation_generate, GenerationResult as TranslationResult
from src.generator.copywriting import generate as copywriting_generate, GenerationResult as CopywritingResult
from src.generator.business_plan import generate as business_plan_generate, GenerationResult as BusinessPlanResult
from src.generator.practice_report import generate as practice_report_generate, GenerationResult as PracticeReportResult
from src.generator.review import generate as review_generate, GenerationResult as ReviewResult
from src.generator.uniqueness import generate as uniqueness_generate, GenerationResult as UniquenessResult
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

    def test_coursework_supported(self):
        """Курсовая работа поддерживается."""
        assert is_supported("Курсовая работа") is True

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
        assert len(types) >= 20
        assert "Эссе" in types
        assert "Реферат" in types
        assert "Курсовая работа" in types
        assert "Дипломная работа" in types
        assert "Контрольная работа" in types
        assert "Бизнес-план" in types

    @pytest.mark.asyncio
    async def test_generate_work_unsupported(self):
        """generate_work для неподдерживаемого типа → None."""
        result = await generate_work(
            work_type="Онлайн-консультация",
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


# ===== Мок-хелперы для multi-step генераторов =====

def _plan_response(chapters_count=2):
    """Создать мок-ответ для генерации плана."""
    chapters = []
    for i in range(1, chapters_count + 1):
        chapters.append({
            "number": i,
            "title": f"Глава {i}",
            "subsections": [f"{i}.1 Подраздел 1", f"{i}.2 Подраздел 2"],
        })
    return {
        "data": {"title": "Тестовая тема", "chapters": chapters},
        "input_tokens": 200,
        "output_tokens": 150,
        "cost_usd": 0.001,
    }


def _section_response(text="Текст секции. " * 100):
    """Создать мок-ответ для генерации секции."""
    return {
        "content": text,
        "model": "gpt-4o",
        "input_tokens": 500,
        "output_tokens": 1000,
        "total_tokens": 1500,
        "cost_usd": 0.01,
    }


# ===== Тесты генератора курсовых =====

class TestCourseworkGenerator:
    """Тесты генератора курсовых работ."""

    @pytest.mark.asyncio
    async def test_generate_coursework_returns_result(self):
        """Курсовая генерируется и возвращает GenerationResult."""
        with patch("src.generator.coursework.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(2)), \
             patch("src.generator.coursework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await coursework_generate(
                title="Экономика предприятия",
                subject="Экономика",
                pages=30,
            )

        assert isinstance(result, CourseworkResult)
        assert result.work_type == "Курсовая работа"
        assert len(result.text) > 100
        assert result.total_tokens > 0
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_coursework_has_plan(self):
        """Курсовая содержит план."""
        with patch("src.generator.coursework.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(2)), \
             patch("src.generator.coursework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await coursework_generate(title="Тест", pages=25)

        assert result.plan is not None
        assert len(result.plan.chapters) == 2

    @pytest.mark.asyncio
    async def test_coursework_has_structure(self):
        """Курсовая содержит введение, главы, заключение, литературу."""
        with patch("src.generator.coursework.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(2)), \
             patch("src.generator.coursework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await coursework_generate(title="Тест", pages=25)

        text = result.text
        assert "ВВЕДЕНИЕ" in text
        assert "ГЛАВА 1" in text
        assert "ГЛАВА 2" in text
        assert "ЗАКЛЮЧЕНИЕ" in text
        assert "СПИСОК ЛИТЕРАТУРЫ" in text

    @pytest.mark.asyncio
    async def test_coursework_accumulates_tokens(self):
        """Токены накапливаются по всем API вызовам."""
        with patch("src.generator.coursework.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(2)), \
             patch("src.generator.coursework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await coursework_generate(title="Тест", pages=25)

        # План + введение + 2 главы + заключение + литература = минимум 6 вызовов
        assert result.input_tokens >= 200  # план + секции
        assert result.output_tokens >= 150
        assert result.cost_usd > 0.001


# ===== Тесты генератора дипломных =====

class TestDiplomaGenerator:
    """Тесты генератора дипломных работ / ВКР."""

    @pytest.mark.asyncio
    async def test_generate_diploma_returns_result(self):
        """ВКР генерируется и возвращает GenerationResult."""
        with patch("src.generator.diploma.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(3)), \
             patch("src.generator.diploma.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await diploma_generate(
                title="Управление персоналом",
                subject="Менеджмент",
                pages=80,
            )

        assert isinstance(result, DiplomaResult)
        assert result.work_type == "Дипломная работа"
        assert len(result.text) > 100
        assert result.total_tokens > 0

    @pytest.mark.asyncio
    async def test_diploma_has_annotation(self):
        """ВКР содержит аннотацию."""
        with patch("src.generator.diploma.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(3)), \
             patch("src.generator.diploma.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await diploma_generate(title="Тест", pages=80)

        assert "АННОТАЦИЯ" in result.text

    @pytest.mark.asyncio
    async def test_diploma_has_all_sections(self):
        """ВКР содержит все обязательные секции."""
        with patch("src.generator.diploma.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(3)), \
             patch("src.generator.diploma.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await diploma_generate(title="Тест", pages=80)

        text = result.text
        assert "АННОТАЦИЯ" in text
        assert "ВВЕДЕНИЕ" in text
        assert "ГЛАВА 1" in text
        assert "ГЛАВА 2" in text
        assert "ГЛАВА 3" in text
        assert "ЗАКЛЮЧЕНИЕ" in text
        assert "СПИСОК ЛИТЕРАТУРЫ" in text


# ===== Тесты генератора контрольных =====

class TestHomeworkGenerator:
    """Тесты генератора контрольных работ / задач."""

    @pytest.mark.asyncio
    async def test_generate_homework_returns_result(self):
        """Контрольная генерируется и возвращает GenerationResult."""
        with patch("src.generator.homework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await homework_generate(
                title="Контрольная по математике",
                description="Решить 5 задач по линейной алгебре",
                subject="Математика",
                pages=10,
            )

        assert isinstance(result, HomeworkResult)
        assert result.work_type == "Контрольная работа"
        assert len(result.text) > 50
        assert result.total_tokens > 0

    @pytest.mark.asyncio
    async def test_homework_single_api_call(self):
        """Контрольная — один вызов API."""
        with patch("src.generator.homework.chat_completion", new_callable=AsyncMock, return_value=_section_response()) as mock_call:
            result = await homework_generate(title="Тест", pages=5)

        mock_call.assert_called_once()


# ===== Тесты генератора презентаций =====

class TestPresentationGenerator:
    """Тесты генератора презентаций."""

    @pytest.mark.asyncio
    async def test_generate_presentation_returns_result(self):
        """Презентация генерируется."""
        slides_text = "СЛАЙД 1: Титульный\n- Тема\nЗАМЕТКИ ДОКЛАДЧИКА: Текст\n\n" * 10
        response = _section_response(slides_text)

        with patch("src.generator.presentation.chat_completion", new_callable=AsyncMock, return_value=response):
            result = await presentation_generate(
                title="Экономика организации",
                subject="Экономика",
                pages=15,
            )

        assert isinstance(result, PresentationResult)
        assert result.work_type == "Презентации"
        assert len(result.text) > 50


# ===== Тесты генератора переводов =====

class TestTranslationGenerator:
    """Тесты генератора переводов."""

    @pytest.mark.asyncio
    async def test_generate_translation_returns_result(self):
        """Перевод генерируется."""
        with patch("src.generator.translation.chat_completion", new_callable=AsyncMock, return_value=_section_response("Переведённый текст.")):
            result = await translation_generate(
                title="Перевод статьи по экономике",
                description="The economy of modern Russia...",
                pages=10,
            )

        assert isinstance(result, TranslationResult)
        assert result.work_type == "Перевод"
        assert len(result.text) > 10


# ===== Тесты генератора копирайтинга =====

class TestCopywritingGenerator:
    """Тесты генератора копирайтинга."""

    @pytest.mark.asyncio
    async def test_generate_copywriting_returns_result(self):
        """Копирайтинг генерируется."""
        with patch("src.generator.copywriting.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await copywriting_generate(
                title="Статья для блога о маркетинге",
                pages=5,
            )

        assert isinstance(result, CopywritingResult)
        assert result.work_type == "Копирайтинг"
        assert len(result.text) > 50


# ===== Тесты генератора бизнес-планов =====

class TestBusinessPlanGenerator:
    """Тесты генератора бизнес-планов."""

    @pytest.mark.asyncio
    async def test_generate_business_plan_returns_result(self):
        """Бизнес-план генерируется."""
        with patch("src.generator.business_plan.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await business_plan_generate(
                title="Открытие кофейни",
                subject="Предпринимательство",
                pages=25,
            )

        assert isinstance(result, BusinessPlanResult)
        assert result.work_type == "Бизнес-план"
        assert len(result.text) > 100

    @pytest.mark.asyncio
    async def test_business_plan_has_sections(self):
        """Бизнес-план содержит все стандартные разделы."""
        with patch("src.generator.business_plan.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await business_plan_generate(title="Тест", pages=25)

        text = result.text
        assert "РЕЗЮМЕ ПРОЕКТА" in text
        assert "АНАЛИЗ РЫНКА" in text
        assert "ФИНАНСОВЫЙ ПЛАН" in text
        assert "АНАЛИЗ РИСКОВ" in text

    @pytest.mark.asyncio
    async def test_business_plan_multiple_api_calls(self):
        """Бизнес-план делает несколько API вызовов (по разделам)."""
        with patch("src.generator.business_plan.chat_completion", new_callable=AsyncMock, return_value=_section_response()) as mock_call:
            result = await business_plan_generate(title="Тест", pages=25)

        # 8 стандартных разделов
        assert mock_call.call_count == 8
        assert result.total_tokens > 0


# ===== Тесты генератора отчётов по практике =====

class TestPracticeReportGenerator:
    """Тесты генератора отчётов по практике."""

    @pytest.mark.asyncio
    async def test_generate_practice_report_returns_result(self):
        """Отчёт по практике генерируется."""
        with patch("src.generator.practice_report.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(3)), \
             patch("src.generator.practice_report.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await practice_report_generate(
                title="Практика в ООО Ромашка",
                subject="Менеджмент",
                pages=20,
            )

        assert isinstance(result, PracticeReportResult)
        assert result.work_type == "Отчёт по практике"
        assert len(result.text) > 100

    @pytest.mark.asyncio
    async def test_practice_report_has_structure(self):
        """Отчёт содержит введение и заключение."""
        with patch("src.generator.practice_report.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(3)), \
             patch("src.generator.practice_report.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await practice_report_generate(title="Тест", pages=20)

        assert "ВВЕДЕНИЕ" in result.text
        assert "ЗАКЛЮЧЕНИЕ" in result.text
        assert "СПИСОК ЛИТЕРАТУРЫ" in result.text


# ===== Тесты генератора рецензий =====

class TestReviewGenerator:
    """Тесты генератора рецензий."""

    @pytest.mark.asyncio
    async def test_generate_review_returns_result(self):
        """Рецензия генерируется."""
        with patch("src.generator.review.chat_completion", new_callable=AsyncMock, return_value=_section_response("Рецензия на работу...")):
            result = await review_generate(
                title="Рецензия на курсовую по экономике",
                pages=3,
            )

        assert isinstance(result, ReviewResult)
        assert result.work_type == "Рецензия"
        assert len(result.text) > 10


# ===== Тесты генератора повышения уникальности =====

class TestUniquenessGenerator:
    """Тесты генератора повышения уникальности."""

    @pytest.mark.asyncio
    async def test_generate_uniqueness_returns_result(self):
        """Повышение уникальности работает."""
        with patch("src.generator.uniqueness.chat_completion", new_callable=AsyncMock, return_value=_section_response("Перефразированный текст.")):
            result = await uniqueness_generate(
                title="Повышение уникальности курсовой",
                description="Исходный текст для рерайта...",
                required_uniqueness=80,
                pages=10,
            )

        assert isinstance(result, UniquenessResult)
        assert result.work_type == "Повышение уникальности текста"
        assert len(result.text) > 10

    @pytest.mark.asyncio
    async def test_uniqueness_uses_description_as_source(self):
        """Рерайт использует description как исходный текст."""
        with patch("src.generator.uniqueness.chat_completion", new_callable=AsyncMock, return_value=_section_response()) as mock_call:
            result = await uniqueness_generate(
                title="Тест",
                description="Текст для повышения уникальности",
                pages=5,
            )

        call_args = mock_call.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        user_msg = messages[-1]["content"]
        assert "Текст для повышения уникальности" in user_msg


# ===== Тесты роутера — расширенные =====

class TestRouterExtended:
    """Расширенные тесты роутера для новых генераторов."""

    def test_default_pages_coursework(self):
        """Дефолтные страницы для курсовой = 30."""
        assert _default_pages("Курсовая работа") == 30

    def test_default_pages_diploma(self):
        """Дефолтные страницы для дипломной = 80."""
        assert _default_pages("Дипломная работа") == 80

    def test_default_pages_homework(self):
        """Дефолтные страницы для контрольной = 10."""
        assert _default_pages("Контрольная работа") == 10

    def test_default_pages_business_plan(self):
        """Дефолтные страницы для бизнес-плана = 25."""
        assert _default_pages("Бизнес-план") == 25

    def test_default_pages_review(self):
        """Дефолтные страницы для рецензии = 3."""
        assert _default_pages("Рецензия") == 3

    def test_all_new_types_supported(self):
        """Все новые типы работ поддерживаются."""
        new_types = [
            "Курсовая работа", "Дипломная работа",
            "Выпускная квалификационная работа (ВКР)",
            "Контрольная работа", "Решение задач", "Ответы на вопросы",
            "Лабораторная работа", "Презентации", "Перевод",
            "Копирайтинг", "Набор текста",
            "Повышение уникальности текста", "Гуманизация работы",
            "Бизнес-план", "Отчёт по практике",
            "Рецензия", "Вычитка и рецензирование работ", "Проверка работы",
            "Монография", "Научно-исследовательская работа (НИР)",
            "Индивидуальный проект", "Маркетинговое исследование",
            "Другое",
        ]
        for wt in new_types:
            assert is_supported(wt), f"Тип '{wt}' не поддерживается"

    def test_realtime_types_not_supported(self):
        """Реалтайм-типы остаются неподдерживаемыми."""
        assert not is_supported("Онлайн-консультация")
        assert not is_supported("Помощь on-line")
        assert not is_supported("Подбор темы работы")

    @pytest.mark.asyncio
    async def test_generate_work_coursework(self):
        """generate_work для курсовой вызывает генератор."""
        with patch("src.generator.coursework.chat_completion_json", new_callable=AsyncMock, return_value=_plan_response(2)), \
             patch("src.generator.coursework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await generate_work(
                work_type="Курсовая работа",
                title="Тестовая курсовая",
                subject="Экономика",
            )

        assert result is not None
        assert result.work_type == "Курсовая работа"

    @pytest.mark.asyncio
    async def test_generate_work_homework(self):
        """generate_work для контрольной вызывает генератор."""
        with patch("src.generator.homework.chat_completion", new_callable=AsyncMock, return_value=_section_response()):
            result = await generate_work(
                work_type="Контрольная работа",
                title="Контрольная по физике",
            )

        assert result is not None
        assert result.work_type == "Контрольная работа"
