"""Тесты генераторов: все типы работ, роутер, DOCX builder, проверка объёма."""

from unittest.mock import AsyncMock, patch

import pytest

from src.generator.essay import generate as essay_generate, GenerationResult as EssayResult
from src.generator.referat import (
    generate as referat_generate, GenerationResult as ReferatResult,
    ReferatPlan, _plan_to_text,
)
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
from src.generator.stepwise import CHARS_PER_PAGE, WORDS_PER_PAGE
from src.generator.router import (
    get_generator, is_supported, supported_types, generate_work, _default_pages,
)
from src.docgen.builder import _sections_from_text, _split_by_markers
from src.docgen.formatter import normalize_text, estimate_pages, split_into_paragraphs


# ===== Mock paths (all generators route through stepwise) =====

MOCK_PLAN = "src.generator.stepwise.chat_completion_json"
MOCK_TEXT = "src.generator.stepwise.chat_completion"


# ===== Mock helpers =====

def _make_plan(section_names: list[str], pages: int) -> dict:
    """Mock plan response for stepwise.generate_plan()."""
    total_words = pages * WORDS_PER_PAGE
    words_per = total_words // len(section_names)
    return {
        "data": {
            "sections": [
                {"name": name, "target_words": words_per}
                for name in section_names
            ],
        },
        "input_tokens": 200,
        "output_tokens": 150,
        "cost_usd": 0.001,
    }


def _make_text(chars: int = 3000) -> dict:
    """Mock text response for stepwise.generate_section()."""
    base = "Текст раздела с примерами, аргументацией и детальным анализом. "
    text = (base * (chars // len(base) + 1))[:chars]
    return {
        "content": text,
        "model": "gpt-4o",
        "input_tokens": 500,
        "output_tokens": 1000,
        "total_tokens": 1500,
        "cost_usd": 0.01,
    }


def _mocks(pages: int, section_names: list[str] | None = None):
    """Return (plan_response, text_response) sized to meet page target."""
    if section_names is None:
        section_names = ["Введение", "Основная часть", "Заключение"]
    target = pages * CHARS_PER_PAGE
    chars_per = (target // len(section_names)) + 500
    return _make_plan(section_names, pages), _make_text(chars_per)


# ===== Тесты генератора эссе =====

class TestEssayGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """generate возвращает GenerationResult с корректными полями."""
        plan, text = _mocks(5)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await essay_generate(
                title="Свобода и ответственность", subject="Философия", pages=5,
            )
        assert isinstance(result, EssayResult)
        assert result.title == "Свобода и ответственность"
        assert result.work_type == "Эссе"
        assert result.total_tokens > 0
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_text_not_empty(self):
        """Сгенерированный текст не пуст."""
        plan, text = _mocks(3)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await essay_generate(title="Тест", pages=3)
        assert len(result.text) > 100
        assert result.pages_approx >= 1


# ===== Тесты генератора рефератов =====

class TestReferatGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Реферат генерируется пошагово и возвращает результат."""
        sections = ["Введение", "Глава 1. Ранний период", "Глава 2. Новое время",
                     "Заключение", "Список литературы"]
        plan, text = _mocks(12, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await referat_generate(
                title="История России XIX века", subject="История", pages=12,
            )
        assert isinstance(result, ReferatResult)
        assert result.work_type == "Реферат"
        assert len(result.text) > 100
        assert result.total_tokens > 0
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_plan_generated(self):
        """План реферата генерируется: главы без введения/заключения/литературы."""
        sections = ["Введение", "Глава 1. Основы", "Заключение", "Список литературы"]
        plan, text = _mocks(10, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await referat_generate(title="Тест", pages=10)
        assert result.plan is not None
        assert len(result.plan.chapters) == 1
        assert result.plan.chapters[0]["title"] == "Глава 1. Основы"

    def test_plan_to_text(self):
        """Преобразование плана в текст."""
        plan = ReferatPlan(
            title="Тест",
            chapters=[
                {"title": "Глава 1"},
                {"title": "Глава 2"},
            ],
        )
        text = _plan_to_text(plan)
        assert "Введение" in text
        assert "Глава 1" in text
        assert "Глава 2" in text
        assert "Заключение" in text
        assert "Список литературы" in text


# ===== Тесты генератора курсовых =====

class TestCourseworkGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Курсовая генерируется и возвращает GenerationResult."""
        sections = ["Введение", "Глава 1. Теория", "1.1 Определения",
                     "Глава 2. Практика", "2.1 Анализ", "Заключение", "Список литературы"]
        plan, text = _mocks(25, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await coursework_generate(
                title="Экономика предприятия", subject="Экономика", pages=25,
            )
        assert isinstance(result, CourseworkResult)
        assert result.work_type == "Курсовая работа"
        assert len(result.text) > 100
        assert result.total_tokens > 0

    @pytest.mark.asyncio
    async def test_has_plan(self):
        """Курсовая содержит план с главами."""
        sections = ["Введение", "Глава 1. Теория", "Глава 2. Анализ",
                     "Заключение", "Список литературы"]
        plan, text = _mocks(25, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await coursework_generate(title="Тест", pages=25)
        assert result.plan is not None
        assert len(result.plan.chapters) == 2

    @pytest.mark.asyncio
    async def test_accumulates_tokens(self):
        """Токены накапливаются по всем API вызовам."""
        sections = ["Введение", "Глава 1. Теория", "Заключение"]
        plan, text = _mocks(25, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await coursework_generate(title="Тест", pages=25)
        assert result.input_tokens >= 200
        assert result.output_tokens >= 150
        assert result.cost_usd > 0.001


# ===== Тесты генератора дипломных =====

class TestDiplomaGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """ВКР генерируется и возвращает GenerationResult."""
        sections = [
            "Аннотация", "Введение",
            "Глава 1. Теоретические основы", "1.1 Обзор литературы",
            "Глава 2. Анализ", "2.1 Текущее состояние",
            "Глава 3. Рекомендации", "3.1 Предложения",
            "Заключение", "Список литературы",
        ]
        plan, text = _mocks(60, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await diploma_generate(
                title="Управление персоналом", subject="Менеджмент", pages=60,
            )
        assert isinstance(result, DiplomaResult)
        assert result.work_type == "Дипломная работа"
        assert len(result.text) > 100

    @pytest.mark.asyncio
    async def test_has_annotation_and_sections(self):
        """ВКР содержит все обязательные секции."""
        sections = [
            "Аннотация", "Введение", "Глава 1. Теория", "Глава 2. Анализ",
            "Глава 3. Рекомендации", "Заключение", "Список литературы",
        ]
        plan, text = _mocks(60, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await diploma_generate(title="Тест", pages=60)
        assembled = result.text
        assert "АННОТАЦИЯ" in assembled
        assert "ВВЕДЕНИЕ" in assembled
        assert "ГЛАВА 1" in assembled
        assert "ГЛАВА 2" in assembled
        assert "ГЛАВА 3" in assembled
        assert "ЗАКЛЮЧЕНИЕ" in assembled
        assert "СПИСОК ЛИТЕРАТУРЫ" in assembled


# ===== Тесты генератора контрольных =====

class TestHomeworkGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Контрольная генерируется пошагово."""
        plan, text = _mocks(8, ["Задача 1", "Задача 2", "Задача 3"])
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await homework_generate(
                title="Контрольная по математике",
                description="Решить задачи по линейной алгебре",
                subject="Математика",
                pages=8,
            )
        assert isinstance(result, HomeworkResult)
        assert result.work_type == "Контрольная работа"
        assert len(result.text) > 50
        assert result.total_tokens > 0


# ===== Тесты генератора презентаций =====

class TestPresentationGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Презентация генерируется пошагово."""
        sections = ["Слайды 1-5: Введение", "Слайды 6-10: Основная часть",
                     "Слайды 11-15: Заключение"]
        plan, text = _mocks(15, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await presentation_generate(
                title="Экономика организации", subject="Экономика", pages=15,
            )
        assert isinstance(result, PresentationResult)
        assert result.work_type == "Презентации"
        assert len(result.text) > 50


# ===== Тесты генератора переводов =====

class TestTranslationGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Перевод генерируется пошагово."""
        plan, text = _mocks(10)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
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

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Копирайтинг генерируется пошагово."""
        plan, text = _mocks(5)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await copywriting_generate(title="Статья для блога о маркетинге", pages=5)
        assert isinstance(result, CopywritingResult)
        assert result.work_type == "Копирайтинг"
        assert len(result.text) > 50


# ===== Тесты генератора бизнес-планов =====

class TestBusinessPlanGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Бизнес-план генерируется пошагово."""
        sections = ["Резюме проекта", "Описание продукта", "Анализ рынка",
                     "Маркетинговая стратегия", "Финансовый план", "Анализ рисков"]
        plan, text = _mocks(15, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await business_plan_generate(
                title="Открытие кофейни", subject="Предпринимательство", pages=15,
            )
        assert isinstance(result, BusinessPlanResult)
        assert result.work_type == "Бизнес-план"
        assert len(result.text) > 100


# ===== Тесты генератора отчётов по практике =====

class TestPracticeReportGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Отчёт по практике генерируется пошагово."""
        sections = ["Введение", "Характеристика организации", "Выполненные работы",
                     "Заключение", "Список литературы"]
        plan, text = _mocks(20, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await practice_report_generate(
                title="Практика в ООО Ромашка", subject="Менеджмент", pages=20,
            )
        assert isinstance(result, PracticeReportResult)
        assert result.work_type == "Отчёт по практике"
        assert len(result.text) > 100


# ===== Тесты генератора рецензий =====

class TestReviewGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Рецензия генерируется пошагово."""
        sections = ["Актуальность", "Оценка структуры", "Достоинства",
                     "Замечания", "Общий вывод"]
        plan, text = _mocks(3, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await review_generate(title="Рецензия на курсовую", pages=3)
        assert isinstance(result, ReviewResult)
        assert result.work_type == "Рецензия"
        assert len(result.text) > 10


# ===== Тесты генератора повышения уникальности =====

class TestUniquenessGenerator:

    @pytest.mark.asyncio
    async def test_generate_returns_result(self):
        """Повышение уникальности работает пошагово."""
        plan, text = _mocks(10)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await uniqueness_generate(
                title="Повышение уникальности курсовой",
                description="Исходный текст для рерайта...",
                required_uniqueness=80,
                pages=10,
            )
        assert isinstance(result, UniquenessResult)
        assert result.work_type == "Повышение уникальности текста"
        assert len(result.text) > 10


# ===== Тесты роутера =====

class TestRouter:

    def test_essay_supported(self):
        assert is_supported("Эссе") is True

    def test_referat_supported(self):
        assert is_supported("Реферат") is True

    def test_coursework_supported(self):
        assert is_supported("Курсовая работа") is True

    def test_get_generator_essay(self):
        gen = get_generator("Эссе")
        assert gen is not None
        assert callable(gen)

    def test_get_generator_unknown(self):
        assert get_generator("Несуществующий тип") is None

    def test_supported_types_not_empty(self):
        types = supported_types()
        assert len(types) >= 20
        for wt in ["Эссе", "Реферат", "Курсовая работа", "Дипломная работа",
                    "Контрольная работа", "Бизнес-план"]:
            assert wt in types

    @pytest.mark.asyncio
    async def test_generate_work_unsupported(self):
        result = await generate_work(work_type="Онлайн-консультация", title="Тест")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_work_essay(self):
        plan, text = _mocks(5)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await generate_work(
                work_type="Эссе", title="Тестовое эссе", subject="Философия",
            )
        assert result is not None
        assert result.work_type == "Эссе"

    @pytest.mark.asyncio
    async def test_generate_work_coursework(self):
        sections = ["Введение", "Глава 1", "Глава 2", "Заключение", "Список литературы"]
        plan, text = _mocks(25, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await generate_work(
                work_type="Курсовая работа", title="Тестовая курсовая", subject="Экономика",
            )
        assert result is not None
        assert result.work_type == "Курсовая работа"

    @pytest.mark.asyncio
    async def test_generate_work_homework(self):
        plan, text = _mocks(8, ["Задача 1", "Задача 2", "Задача 3"])
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await generate_work(
                work_type="Контрольная работа", title="Контрольная по физике",
            )
        assert result is not None
        assert result.work_type == "Контрольная работа"

    # --- Дефолтные страницы (обновлённые значения) ---

    def test_default_pages_essay(self):
        assert _default_pages("Эссе") == 5

    def test_default_pages_referat(self):
        assert _default_pages("Реферат") == 12

    def test_default_pages_doklad(self):
        assert _default_pages("Доклад") == 10

    def test_default_pages_coursework(self):
        assert _default_pages("Курсовая работа") == 25

    def test_default_pages_diploma(self):
        assert _default_pages("Дипломная работа") == 60

    def test_default_pages_homework(self):
        assert _default_pages("Контрольная работа") == 8

    def test_default_pages_tasks(self):
        assert _default_pages("Решение задач") == 3

    def test_default_pages_business_plan(self):
        assert _default_pages("Бизнес-план") == 15

    def test_default_pages_practice_report(self):
        assert _default_pages("Отчёт по практике") == 20

    def test_default_pages_review(self):
        assert _default_pages("Рецензия") == 3

    def test_all_generators_mapped(self):
        from src.generator.router import GENERATORS
        for wt in ["Эссе", "Реферат", "Курсовая работа", "Дипломная работа",
                    "Контрольная работа"]:
            assert wt in GENERATORS

    def test_all_new_types_supported(self):
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
        assert not is_supported("Онлайн-консультация")
        assert not is_supported("Помощь on-line")
        assert not is_supported("Подбор темы работы")

    def test_is_banned_with_config(self):
        """is_banned возвращает True для типов из BANNED_WORK_TYPES."""
        from src.generator.router import is_banned
        with patch("src.config.settings") as mock_settings:
            mock_settings.banned_work_types_list = ["Чертёж", "Онлайн-консультация", "Монография"]
            assert is_banned("Чертёж") is True
            assert is_banned("Онлайн-консультация") is True
            assert is_banned("Монография") is True
            assert is_banned("Эссе") is False
            assert is_banned("Курсовая работа") is False

    def test_is_banned_empty_config(self):
        """is_banned возвращает False когда BANNED_WORK_TYPES пуст."""
        from src.generator.router import is_banned
        with patch("src.config.settings") as mock_settings:
            mock_settings.banned_work_types_list = []
            assert is_banned("Чертёж") is False
            assert is_banned("Эссе") is False


# ===== Тесты проверки минимального объёма (volume compliance) =====

class TestVolumeCompliance:
    """Каждый генератор должен выдавать текст >= pages * CHARS_PER_PAGE."""

    @pytest.mark.asyncio
    async def test_essay_volume(self):
        pages = 5
        plan, text = _mocks(pages)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await essay_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_referat_volume(self):
        pages = 12
        sections = ["Введение", "Глава 1", "Глава 2", "Глава 3",
                     "Заключение", "Список литературы"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await referat_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_coursework_volume(self):
        pages = 25
        sections = ["Введение", "Глава 1. Теория", "Глава 2. Анализ",
                     "Заключение", "Список литературы"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await coursework_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_diploma_volume(self):
        pages = 60
        sections = [
            "Аннотация", "Введение", "Глава 1. Теория", "Глава 2. Анализ",
            "Глава 3. Рекомендации", "Заключение", "Список литературы",
        ]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await diploma_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_homework_volume(self):
        pages = 8
        plan, text = _mocks(pages, ["Задача 1", "Задача 2", "Задача 3"])
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await homework_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_presentation_volume(self):
        pages = 15
        sections = ["Слайды 1-5", "Слайды 6-10", "Слайды 11-15"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await presentation_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_translation_volume(self):
        pages = 10
        plan, text = _mocks(pages)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await translation_generate(
                title="Тест", description="Source text", pages=pages,
            )
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_copywriting_volume(self):
        pages = 5
        plan, text = _mocks(pages)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await copywriting_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_business_plan_volume(self):
        pages = 15
        sections = ["Резюме", "Описание продукта", "Анализ рынка",
                     "Маркетинг", "Финансы", "Риски"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await business_plan_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_practice_report_volume(self):
        pages = 20
        sections = ["Введение", "Характеристика", "Выполненные работы",
                     "Анализ опыта", "Заключение", "Список литературы"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await practice_report_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_review_volume(self):
        pages = 3
        sections = ["Актуальность", "Оценка", "Достоинства", "Замечания", "Вывод"]
        plan, text = _mocks(pages, sections)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await review_generate(title="Тест", pages=pages)
        assert len(result.text) >= pages * CHARS_PER_PAGE

    @pytest.mark.asyncio
    async def test_uniqueness_volume(self):
        pages = 10
        plan, text = _mocks(pages)
        with patch(MOCK_PLAN, new_callable=AsyncMock, return_value=plan), \
             patch(MOCK_TEXT, new_callable=AsyncMock, return_value=text):
            result = await uniqueness_generate(
                title="Тест", description="Исходный текст", pages=pages,
            )
        assert len(result.text) >= pages * CHARS_PER_PAGE


# ===== Тесты DOCX builder =====

class TestDocxBuilder:

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

    def test_normalize_text_removes_double_spaces(self):
        assert normalize_text("Текст  с  пробелами") == "Текст с пробелами"

    def test_normalize_text_fixes_punctuation(self):
        assert normalize_text("Текст .") == "Текст."

    def test_normalize_text_limits_newlines(self):
        result = normalize_text("Текст\n\n\n\nТекст2")
        assert "\n\n\n" not in result

    def test_estimate_pages(self):
        text = "x" * 9000
        assert estimate_pages(text) == 5

    def test_estimate_pages_minimum(self):
        assert estimate_pages("короткий") == 1

    def test_split_into_paragraphs(self):
        text = "Первый абзац.\n\nВторой абзац.\n\nТретий абзац."
        paras = split_into_paragraphs(text)
        assert len(paras) == 3
        assert paras[0] == "Первый абзац."

    def test_split_into_paragraphs_ignores_empty(self):
        text = "Текст.\n\n\n\n\n\nЕщё текст."
        paras = split_into_paragraphs(text)
        assert len(paras) == 2


# ===== Тест AI клиента =====

class TestAiClient:

    def test_calculate_cost_gpt4o(self):
        from src.ai_client import calculate_cost
        cost = calculate_cost("gpt-4o", 1000, 1000)
        assert abs(cost - 0.0125) < 0.0001

    def test_calculate_cost_gpt4o_mini(self):
        from src.ai_client import calculate_cost
        cost = calculate_cost("gpt-4o-mini", 1000, 1000)
        assert abs(cost - 0.00075) < 0.00001

    def test_calculate_cost_unknown_model(self):
        from src.ai_client import calculate_cost
        cost = calculate_cost("unknown-model", 1000, 1000)
        assert cost > 0
