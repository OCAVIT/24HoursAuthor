"""Тесты анализатора: скоринг, расчёт цен, анализ файлов."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.scraper.order_detail import OrderDetail
from src.analyzer.order_scorer import score_order, ScoreResult, _build_order_prompt
from src.analyzer.price_calculator import (
    calculate_price, _try_budget_based, _try_average_bid_based,
    _formula_based, _default_pages, _complexity_factor, MIN_BID,
)
from src.analyzer.file_analyzer import extract_text, extract_text_from_pdf, extract_text_from_docx


# ===== Хелперы =====

def _make_order(**kwargs) -> OrderDetail:
    """Создать OrderDetail с дефолтными значениями."""
    defaults = {
        "order_id": "10001",
        "title": "Тестовый заказ",
        "url": "https://avtor24.ru/order/10001",
        "work_type": "Эссе",
        "subject": "Философия",
        "description": "Напишите эссе на тему свободы",
        "budget": "1500₽",
        "budget_rub": 1500,
        "pages_min": 5,
        "pages_max": 7,
        "required_uniqueness": 60,
        "antiplagiat_system": "ETXT",
        "deadline": "15.02.2026",
        "average_bid": 1200,
    }
    defaults.update(kwargs)
    return OrderDetail(**defaults)


# ===== Тесты скоринга =====

class TestOrderScorer:
    """Тесты скоринга заказов."""

    @pytest.mark.asyncio
    async def test_score_order_returns_score_result(self):
        """score_order возвращает ScoreResult."""
        order = _make_order()

        mock_response = {
            "data": {
                "score": 75,
                "can_do": True,
                "estimated_time_min": 30,
                "estimated_cost_rub": 5,
                "reason": "Эссе по философии — простой тип работы",
            },
            "input_tokens": 200,
            "output_tokens": 100,
            "cost_usd": 0.001,
        }

        with patch("src.analyzer.order_scorer.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await score_order(order)

        assert isinstance(result, ScoreResult)
        assert result.score == 75
        assert result.can_do is True
        assert result.estimated_time_min == 30
        assert result.reason == "Эссе по философии — простой тип работы"

    @pytest.mark.asyncio
    async def test_score_clamps_to_0_100(self):
        """score ограничивается диапазоном 0-100."""
        order = _make_order()

        mock_response = {
            "data": {"score": 150, "can_do": True, "estimated_time_min": 10, "estimated_cost_rub": 1, "reason": ""},
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001,
        }

        with patch("src.analyzer.order_scorer.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await score_order(order)

        assert result.score == 100

    @pytest.mark.asyncio
    async def test_score_negative_clamps_to_0(self):
        """Отрицательный score → 0."""
        order = _make_order()

        mock_response = {
            "data": {"score": -10, "can_do": False, "estimated_time_min": 0, "estimated_cost_rub": 0, "reason": "Нет"},
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001,
        }

        with patch("src.analyzer.order_scorer.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await score_order(order)

        assert result.score == 0
        assert result.can_do is False

    @pytest.mark.asyncio
    async def test_score_handles_empty_response(self):
        """Пустой ответ от AI → дефолтные значения."""
        order = _make_order()

        mock_response = {
            "data": {},
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001,
        }

        with patch("src.analyzer.order_scorer.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await score_order(order)

        assert result.score == 0
        assert result.can_do is False

    def test_build_order_prompt_includes_title(self):
        """Промпт содержит заголовок заказа."""
        order = _make_order(title="Экономический анализ")
        prompt = _build_order_prompt(order)
        assert "Экономический анализ" in prompt

    def test_build_order_prompt_includes_budget(self):
        """Промпт содержит бюджет."""
        order = _make_order(budget=5000)
        prompt = _build_order_prompt(order)
        assert "5000" in prompt

    def test_build_order_prompt_includes_work_type(self):
        """Промпт содержит тип работы."""
        order = _make_order(work_type="Курсовая работа")
        prompt = _build_order_prompt(order)
        assert "Курсовая работа" in prompt

    def test_build_order_prompt_truncates_description(self):
        """Длинное описание обрезается."""
        order = _make_order(description="x" * 2000)
        prompt = _build_order_prompt(order)
        # Описание обрезано до 1000 символов
        assert len(prompt) < 2000

    def test_build_order_prompt_no_budget(self):
        """Без бюджета — нет строки бюджета в промпте."""
        order = _make_order(budget=None)
        prompt = _build_order_prompt(order)
        assert "Бюджет" not in prompt

    def test_build_order_prompt_with_files(self):
        """Промпт упоминает прикреплённые файлы."""
        order = _make_order(file_names=["http://example.com/file1.pdf", "http://example.com/file2.pdf"])
        prompt = _build_order_prompt(order)
        assert "2" in prompt  # 2 файла


# ===== Тесты калькулятора цен =====

class TestPriceCalculator:
    """Тесты расчёта цен."""

    def test_budget_based_pricing(self):
        """Цена на основе бюджета заказчика: 85-95% от бюджета."""
        order = _make_order(budget="3000₽", budget_rub=3000)
        for _ in range(20):
            price = calculate_price(order)
            assert 2550 <= price <= 2850  # 85-95% от 3000

    def test_average_bid_based_pricing(self):
        """Если нет бюджета — используем среднюю ставку."""
        order = _make_order(budget=None, budget_rub=None, average_bid=2000)
        for _ in range(20):
            price = calculate_price(order)
            assert 1800 <= price <= 2000  # 90-100% от 2000

    def test_formula_based_pricing(self):
        """Если нет ни бюджета ни ставок — формула."""
        order = _make_order(
            budget=None, budget_rub=None, average_bid=None,
            work_type="Эссе", pages_max=5,
        )
        price = calculate_price(order)
        # Эссе: 150 руб/стр × 5 стр × ~1.0 = ~750
        assert price >= MIN_BID
        assert price <= 2000

    def test_minimum_bid(self):
        """Цена не может быть меньше MIN_BID."""
        order = _make_order(budget="100₽", budget_rub=100, average_bid=None)
        price = calculate_price(order)
        assert price >= MIN_BID

    def test_referat_price(self):
        """Цена реферата по формуле."""
        order = _make_order(
            budget=None, budget_rub=None, average_bid=None,
            work_type="Реферат", pages_max=15,
        )
        price = calculate_price(order)
        # 120 руб/стр × 15 = 1800
        assert price >= 1500
        assert price <= 2500

    def test_coursework_price(self):
        """Цена курсовой по формуле."""
        order = _make_order(
            budget=None, budget_rub=None, average_bid=None,
            work_type="Курсовая работа", pages_max=30,
        )
        price = calculate_price(order)
        # 200 руб/стр × 30 = 6000
        assert price >= 5000
        assert price <= 8000

    def test_complexity_factor_high_uniqueness(self):
        """Высокая уникальность увеличивает коэффициент."""
        order_low = _make_order(required_uniqueness=50)
        order_high = _make_order(required_uniqueness=90)
        assert _complexity_factor(order_high) > _complexity_factor(order_low)

    def test_complexity_factor_with_files(self):
        """Наличие файлов увеличивает коэффициент."""
        order_no_files = _make_order(file_names=[])
        order_files = _make_order(file_names=["file1.pdf"])
        assert _complexity_factor(order_files) > _complexity_factor(order_no_files)

    def test_default_pages(self):
        """Дефолтные страницы для типов работ."""
        assert _default_pages("Эссе") == 5
        assert _default_pages("Реферат") == 15
        assert _default_pages("Курсовая работа") == 30
        assert _default_pages("Дипломная работа") == 80

    def test_try_budget_based_none_budget(self):
        """Без бюджета → None."""
        order = _make_order(budget=None, budget_rub=None)
        assert _try_budget_based(order) is None

    def test_try_average_bid_based_none(self):
        """Без средней ставки → None."""
        order = _make_order(average_bid=None)
        assert _try_average_bid_based(order) is None


# ===== Тесты анализа файлов =====

class TestFileAnalyzer:
    """Тесты анализатора файлов."""

    def test_extract_text_from_txt(self, tmp_path):
        """Извлечение текста из .txt файла."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Тестовый текст из файла", encoding="utf-8")
        result = extract_text(txt_file)
        assert "Тестовый текст" in result

    def test_extract_text_unsupported_format(self, tmp_path):
        """Неподдерживаемый формат → пустая строка."""
        file = tmp_path / "test.xyz"
        file.write_text("data")
        result = extract_text(file)
        assert result == ""

    def test_extract_text_pdf_invalid_file(self, tmp_path):
        """Невалидный PDF → пустая строка (graceful, не падает)."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")
        result = extract_text_from_pdf(pdf_file)
        # Невалидный PDF — функция должна вернуть пустую строку и не упасть
        assert isinstance(result, str)

    def test_extract_text_docx_missing_module(self, tmp_path):
        """DOCX без python-docx → пустая строка (graceful)."""
        docx_file = tmp_path / "test.docx"
        docx_file.write_bytes(b"PK fake docx")
        result = extract_text_from_docx(docx_file)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_summarize_files_empty(self):
        """Пустой список файлов → None."""
        from src.analyzer.file_analyzer import summarize_files
        result = await summarize_files([])
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_files_with_text(self, tmp_path):
        """Суммаризация текстового файла."""
        from src.analyzer.file_analyzer import summarize_files

        txt_file = tmp_path / "methodology.txt"
        txt_file.write_text(
            "Методические указания по написанию курсовой работы.\n"
            "Шрифт: Times New Roman 14pt, интервал 1.5.\n"
            "Объём: 25-30 страниц.",
            encoding="utf-8",
        )

        mock_response = {
            "content": (
                "КРАТКОЕ СОДЕРЖАНИЕ: Методичка по написанию курсовой.\n"
                "ТРЕБОВАНИЯ К ОФОРМЛЕНИЮ: Times New Roman 14pt, 1.5 интервал.\n"
                "СТРУКТУРА РАБОТЫ: Введение, 3 главы, заключение.\n"
                "ОБЪЁМ: 25-30 страниц."
            ),
            "input_tokens": 150,
            "output_tokens": 80,
            "cost_usd": 0.001,
        }

        with patch("src.analyzer.file_analyzer.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await summarize_files([txt_file])

        assert result is not None
        assert "summary" in result
        assert result["input_tokens"] == 150
        assert result["cost_usd"] == 0.001


# ===== Тесты трекинга API (через существующий CRUD) =====

class TestApiUsageTracking:
    """Тесты трекинга использования API."""

    @pytest.mark.asyncio
    async def test_track_api_usage(self, session):
        """Запись использования API в БД."""
        from src.database.crud import track_api_usage

        usage = await track_api_usage(
            session,
            model="gpt-4o-mini",
            purpose="scoring",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.001,
        )
        assert usage.id is not None
        assert usage.model == "gpt-4o-mini"
        assert usage.purpose == "scoring"
        assert usage.input_tokens == 200

    @pytest.mark.asyncio
    async def test_track_api_usage_with_order(self, session):
        """Запись использования API привязывается к заказу."""
        from src.database.crud import create_order, track_api_usage

        order = await create_order(session, avtor24_id="55555", title="Тест API")
        usage = await track_api_usage(
            session,
            model="gpt-4o",
            purpose="generation",
            input_tokens=1000,
            output_tokens=5000,
            cost_usd=0.06,
            order_id=order.id,
        )
        assert usage.order_id == order.id
        assert usage.cost_usd == 0.06


# ===== Тесты file_analyzer: vision / image detection =====

class TestFileAnalyzerVision:
    """Тесты обработки изображений и vision API."""

    def test_is_image_file_png(self, tmp_path):
        """PNG определяется как изображение."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.png") is True

    def test_is_image_file_jpg(self, tmp_path):
        """JPG определяется как изображение."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.jpg") is True

    def test_is_image_file_jpeg(self, tmp_path):
        """JPEG определяется как изображение."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.jpeg") is True

    def test_is_image_file_heic(self, tmp_path):
        """HEIC определяется как изображение."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.heic") is True

    def test_is_image_file_pdf_not_image(self, tmp_path):
        """PDF НЕ является изображением."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.pdf") is False

    def test_is_image_file_docx_not_image(self, tmp_path):
        """DOCX НЕ является изображением."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "test.docx") is False

    def test_is_image_file_txt_not_image(self, tmp_path):
        """TXT НЕ является изображением."""
        from src.analyzer.file_analyzer import is_image_file
        assert is_image_file(tmp_path / "readme.txt") is False

    @pytest.mark.asyncio
    async def test_extract_all_content_text_only(self, tmp_path):
        """extract_all_content с текстовым файлом — без vision."""
        from src.analyzer.file_analyzer import extract_all_content
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Тестовый контент файла.", encoding="utf-8")
        result = await extract_all_content([txt_file])
        assert "Тестовый контент" in result.text
        assert result.total_cost_usd == 0.0
        assert result.vision_texts == []

    @pytest.mark.asyncio
    async def test_extract_all_content_empty_list(self):
        """extract_all_content с пустым списком — пустой результат."""
        from src.analyzer.file_analyzer import extract_all_content
        result = await extract_all_content([])
        assert result.text == ""
        assert result.vision_texts == []
        assert result.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_extract_all_content_image_calls_vision(self, tmp_path):
        """extract_all_content с изображением вызывает vision API."""
        from src.analyzer.file_analyzer import extract_all_content
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_vision = {
            "text": "Текст с изображения",
            "input_tokens": 500,
            "output_tokens": 100,
            "cost_usd": 0.005,
        }

        with patch("src.analyzer.file_analyzer.extract_text_from_image", new_callable=AsyncMock, return_value=mock_vision):
            result = await extract_all_content([img_file])

        assert len(result.vision_texts) == 1
        assert "Текст с изображения" in result.vision_texts[0]
        assert result.total_cost_usd == 0.005

    def test_extract_text_returns_empty_for_images(self, tmp_path):
        """extract_text возвращает пустую строку для изображений."""
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        result = extract_text(img_file)
        assert result == ""


# ===== Тесты field_extractor =====

class TestFieldExtractor:
    """Тесты извлечения недостающих полей из описания/файлов."""

    @pytest.mark.asyncio
    async def test_extract_fills_empty_pages(self):
        """Пустые pages заполняются из описания."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="100", title="Тест", url="https://test.com",
            description="Курсовая работа 25-30 страниц, уникальность 70%",
        )

        mock_response = {
            "data": {"pages_min": 25, "pages_max": 30, "required_uniqueness": 70},
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0005,
        }

        with patch("src.analyzer.field_extractor.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await extract_missing_fields(order)

        assert result.order.pages_min == 25
        assert result.order.pages_max == 30
        assert result.order.required_uniqueness == 70
        assert "pages_min" in result.fields_extracted
        assert "pages_max" in result.fields_extracted
        assert result.order.extracted_from_files is True

    @pytest.mark.asyncio
    async def test_extract_preserves_existing_fields(self):
        """Уже заполненные поля НЕ перезаписываются."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="101", title="Тест", url="https://test.com",
            description="Реферат 15-20 стр",
            pages_min=10, pages_max=15, required_uniqueness=60,
        )

        mock_response = {
            "data": {"pages_min": 20, "pages_max": 25, "required_uniqueness": 80},
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0005,
        }

        with patch("src.analyzer.field_extractor.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await extract_missing_fields(order)

        # Existing values NOT overwritten
        assert result.order.pages_min == 10
        assert result.order.pages_max == 15
        assert result.order.required_uniqueness == 60
        assert "pages_min" not in result.fields_extracted

    @pytest.mark.asyncio
    async def test_extract_no_missing_fields(self):
        """Все поля заполнены — API не вызывается."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="102", title="Тест", url="https://test.com",
            pages_min=10, pages_max=15,
            required_uniqueness=60,
            antiplagiat_system="ETXT",
            font_size=12,
            line_spacing=2.0,
            formatting_requirements="TNR 12",
            structure="Введение, Глава 1, Заключение",
            special_requirements="Методичка прилагается",
        )

        result = await extract_missing_fields(order)
        assert result.fields_extracted == []
        assert result.input_tokens == 0  # No API call made

    @pytest.mark.asyncio
    async def test_extract_empty_description_and_no_files(self):
        """Пустое описание и нет файлов — API не вызывается."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="103", title="Тест", url="https://test.com",
            description="",
        )
        result = await extract_missing_fields(order, files_text="")
        assert result.fields_extracted == []

    @pytest.mark.asyncio
    async def test_extract_fills_formatting_requirements(self):
        """Требования к оформлению извлекаются из текста."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="104", title="Тест", url="https://test.com",
            description="Требования: шрифт Times New Roman 12, интервал 1.0",
        )

        mock_response = {
            "data": {
                "font_size": 12,
                "line_spacing": 1.0,
                "formatting_requirements": "Times New Roman 12pt, интервал 1.0",
            },
            "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0005,
        }

        with patch("src.analyzer.field_extractor.chat_completion_json", new_callable=AsyncMock, return_value=mock_response):
            result = await extract_missing_fields(order)

        assert result.order.font_size == 12
        assert result.order.line_spacing == 1.0
        assert "Times New Roman" in result.order.formatting_requirements
        assert result.order.extracted_from_files is True

    @pytest.mark.asyncio
    async def test_extract_with_files_text(self):
        """Текст файлов передаётся в GPT вместе с описанием."""
        from src.analyzer.field_extractor import extract_missing_fields
        order = OrderDetail(
            order_id="105", title="Тест", url="https://test.com",
            description="Курсовая работа",
        )

        mock_response = {
            "data": {"pages_min": 25, "structure": "Введение, 3 главы, Заключение"},
            "input_tokens": 200, "output_tokens": 80, "cost_usd": 0.001,
        }

        with patch("src.analyzer.field_extractor.chat_completion_json", new_callable=AsyncMock, return_value=mock_response) as mock_call:
            result = await extract_missing_fields(
                order, files_text="Методичка: минимум 25 страниц, 3 главы"
            )

        assert result.order.pages_min == 25
        assert "structure" in result.fields_extracted
        # Проверяем что files_text был передан в промпт
        call_args = mock_call.call_args
        user_msg = call_args[1]["messages"][1]["content"] if "messages" in call_args[1] else call_args[0][0][1]["content"]
        assert "Методичка" in str(user_msg)
