"""Тесты модуля антиплагиата: text.ru, ETXT, checker, rewriter."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.antiplagiat.checker import check_uniqueness, extract_text_from_docx, CheckResult
from src.antiplagiat.rewriter import (
    rewrite_for_uniqueness, RewriteResult,
    _build_rewrite_prompt, _group_paragraphs_into_chunks,
)


# ===== Тесты text.ru API =====

class TestTextRu:
    """Тесты проверки через text.ru."""

    @pytest.mark.asyncio
    async def test_check_returns_uniqueness(self):
        """check() возвращает процент уникальности."""
        mock_submit_response = MagicMock()
        mock_submit_response.status_code = 200
        mock_submit_response.raise_for_status = MagicMock()
        mock_submit_response.json.return_value = {"text_uid": "abc123"}

        mock_result_response = MagicMock()
        mock_result_response.status_code = 200
        mock_result_response.raise_for_status = MagicMock()
        mock_result_response.json.return_value = {"unique": 75.3}

        async def mock_post(url, **kwargs):
            data = kwargs.get("json", {})
            if "text" in data:
                return mock_submit_response
            return mock_result_response

        with patch("src.antiplagiat.textru.settings") as mock_settings, \
             patch("src.antiplagiat.textru.asyncio.sleep", new_callable=AsyncMock), \
             patch("src.antiplagiat.textru.httpx.AsyncClient") as mock_client_cls:
            mock_settings.textru_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from src.antiplagiat.textru import check
            result = await check("Тестовый текст для проверки")

        assert result == 75.3

    @pytest.mark.asyncio
    async def test_check_raises_without_api_key(self):
        """check() бросает ошибку без API ключа."""
        with patch("src.antiplagiat.textru.settings") as mock_settings:
            mock_settings.textru_api_key = ""

            from src.antiplagiat.textru import check
            with pytest.raises(RuntimeError, match="TEXTRU_API_KEY"):
                await check("текст")


# ===== Тесты ETXT API =====

class TestEtxt:
    """Тесты проверки через ETXT."""

    @pytest.mark.asyncio
    async def test_check_returns_uniqueness(self):
        """check() возвращает процент уникальности."""
        mock_submit_response = MagicMock()
        mock_submit_response.status_code = 200
        mock_submit_response.raise_for_status = MagicMock()
        mock_submit_response.json.return_value = {"id": 42}

        mock_result_response = MagicMock()
        mock_result_response.status_code = 200
        mock_result_response.raise_for_status = MagicMock()
        mock_result_response.json.return_value = {"unique": 82.0, "status": "done"}

        async def mock_post(url, **kwargs):
            data = kwargs.get("data", {})
            if data.get("method") == "text_check":
                return mock_submit_response
            return mock_result_response

        with patch("src.antiplagiat.etxt.settings") as mock_settings, \
             patch("src.antiplagiat.etxt.asyncio.sleep", new_callable=AsyncMock), \
             patch("src.antiplagiat.etxt.httpx.AsyncClient") as mock_client_cls:
            mock_settings.etxt_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from src.antiplagiat.etxt import check
            result = await check("Тестовый текст для проверки")

        assert result == 82.0

    @pytest.mark.asyncio
    async def test_check_raises_without_api_key(self):
        """check() бросает ошибку без API ключа."""
        with patch("src.antiplagiat.etxt.settings") as mock_settings:
            mock_settings.etxt_api_key = ""

            from src.antiplagiat.etxt import check
            with pytest.raises(RuntimeError, match="ETXT_API_KEY"):
                await check("текст")


# ===== Тесты checker.py =====

class TestChecker:
    """Тесты основного чекера уникальности."""

    @pytest.mark.asyncio
    async def test_check_uniqueness_with_text(self):
        """check_uniqueness с текстом возвращает CheckResult."""
        with patch("src.antiplagiat.checker._check_with_system", new_callable=AsyncMock, return_value=72.5):
            result = await check_uniqueness(
                text="Тестовый текст для проверки уникальности.",
                system="textru",
                required_uniqueness=50.0,
            )

        assert isinstance(result, CheckResult)
        assert result.uniqueness == 72.5
        assert result.system == "textru"
        assert result.is_sufficient is True
        assert result.required == 50.0

    @pytest.mark.asyncio
    async def test_check_uniqueness_insufficient(self):
        """Уникальность ниже порога → is_sufficient=False."""
        with patch("src.antiplagiat.checker._check_with_system", new_callable=AsyncMock, return_value=35.0):
            result = await check_uniqueness(
                text="Текст с низкой уникальностью.",
                system="textru",
                required_uniqueness=60.0,
            )

        assert result.is_sufficient is False
        assert result.uniqueness == 35.0
        assert result.required == 60.0

    @pytest.mark.asyncio
    async def test_check_uniqueness_default_threshold(self):
        """Без required_uniqueness берётся значение из settings.min_uniqueness."""
        with patch("src.antiplagiat.checker._check_with_system", new_callable=AsyncMock, return_value=55.0), \
             patch("src.antiplagiat.checker.settings") as mock_settings:
            mock_settings.min_uniqueness = 50
            result = await check_uniqueness(text="Текст.", system="textru")

        assert result.required == 50
        assert result.is_sufficient is True

    @pytest.mark.asyncio
    async def test_check_uniqueness_raises_without_input(self):
        """Без текста и файла → ValueError."""
        with pytest.raises(ValueError, match="filepath или text"):
            await check_uniqueness()

    @pytest.mark.asyncio
    async def test_check_uniqueness_raises_on_empty_text(self):
        """Пустой текст → ValueError."""
        with pytest.raises(ValueError, match="Текст пуст"):
            await check_uniqueness(text="   ")

    @pytest.mark.asyncio
    async def test_check_uniqueness_selects_etxt(self):
        """system='etxt' вызывает ETXT API."""
        with patch("src.antiplagiat.checker._check_with_system", new_callable=AsyncMock, return_value=80.0) as mock_check:
            await check_uniqueness(text="Текст.", system="etxt")

        mock_check.assert_called_once_with("Текст.", "etxt")

    @pytest.mark.asyncio
    async def test_check_uniqueness_tracks_text_length(self):
        """CheckResult содержит длину проверенного текста."""
        text = "Абзац текста для проверки уникальности через систему."
        with patch("src.antiplagiat.checker._check_with_system", new_callable=AsyncMock, return_value=90.0):
            result = await check_uniqueness(text=text)

        assert result.text_length == len(text)


# ===== Тесты rewriter.py =====

class TestRewriter:
    """Тесты рерайтера."""

    @pytest.mark.asyncio
    async def test_rewrite_returns_different_text(self):
        """Рерайт возвращает отличающийся от оригинала текст."""
        original = "Экономика предприятия изучает формирование и использование ресурсов."
        rewritten = "Дисциплина исследует процессы создания и применения ресурсной базы организации."

        mock_response = {
            "content": rewritten,
            "model": "gpt-4o",
            "input_tokens": 200,
            "output_tokens": 150,
            "total_tokens": 350,
            "cost_usd": 0.002,
        }

        with patch("src.antiplagiat.rewriter.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await rewrite_for_uniqueness(
                text=original,
                target_percent=70.0,
                current_percent=40.0,
            )

        assert isinstance(result, RewriteResult)
        assert result.text != original
        assert result.text == rewritten
        assert result.input_tokens == 200
        assert result.output_tokens == 150
        assert result.cost_usd == 0.002

    @pytest.mark.asyncio
    async def test_rewrite_tokens_tracked(self):
        """Токены и стоимость отслеживаются."""
        mock_response = {
            "content": "Перефразированный текст.",
            "model": "gpt-4o",
            "input_tokens": 500,
            "output_tokens": 400,
            "total_tokens": 900,
            "cost_usd": 0.005,
        }

        with patch("src.antiplagiat.rewriter.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await rewrite_for_uniqueness(text="Исходный текст.", target_percent=80.0)

        assert result.input_tokens == 500
        assert result.output_tokens == 400
        assert result.total_tokens == 900
        assert result.cost_usd == 0.005
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_rewrite_long_text_split_into_chunks(self):
        """Длинный текст разбивается на чанки для рерайта."""
        # Текст длиннее MAX_CHARS_PER_CALL (14000)
        long_text = "Абзац текста. " * 1200  # ~16800 символов

        mock_response = {
            "content": "Перефразированный чанк. " * 500,
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 250,
            "total_tokens": 550,
            "cost_usd": 0.003,
        }

        with patch("src.antiplagiat.rewriter.chat_completion", new_callable=AsyncMock, return_value=mock_response):
            result = await rewrite_for_uniqueness(text=long_text, target_percent=70.0)

        # Должно быть >1 вызова, значит больше токенов чем один вызов
        assert result.text
        assert result.input_tokens >= 300  # минимум один вызов
        assert result.cost_usd >= 0.003

    def test_build_rewrite_prompt_includes_target(self):
        """Промпт содержит целевую уникальность."""
        prompt = _build_rewrite_prompt("Текст.", target_percent=75.0)
        assert "75%" in prompt

    def test_build_rewrite_prompt_includes_current(self):
        """Промпт содержит текущую уникальность если передана."""
        prompt = _build_rewrite_prompt("Текст.", target_percent=75.0, current_percent=40.0)
        assert "40%" in prompt
        assert "75%" in prompt

    def test_build_rewrite_prompt_includes_text(self):
        """Промпт содержит сам текст для перефразирования."""
        text = "Уникальный фрагмент для проверки."
        prompt = _build_rewrite_prompt(text, target_percent=80.0)
        assert text in prompt

    def test_group_paragraphs_into_chunks_single(self):
        """Короткие абзацы → один чанк."""
        paragraphs = ["Первый абзац.", "Второй абзац."]
        chunks = _group_paragraphs_into_chunks(paragraphs, max_chars=1000)
        assert len(chunks) == 1
        assert "Первый абзац." in chunks[0]
        assert "Второй абзац." in chunks[0]

    def test_group_paragraphs_into_chunks_multiple(self):
        """Длинные абзацы → несколько чанков."""
        paragraphs = ["A" * 500, "B" * 500, "C" * 500]
        chunks = _group_paragraphs_into_chunks(paragraphs, max_chars=600)
        assert len(chunks) >= 2

    def test_group_paragraphs_into_chunks_empty(self):
        """Пустой список → один пустой чанк."""
        chunks = _group_paragraphs_into_chunks([], max_chars=1000)
        assert len(chunks) == 1


# ===== Тесты интеграции: rewrite loop =====

class TestRewriteLoop:
    """Тесты цикла рерайта (через router.generate_and_check)."""

    @pytest.mark.asyncio
    async def test_rewrite_loop_max_3_iterations(self):
        """Максимум 3 итерации рерайта."""
        mock_gen_response = {
            "content": "Текст эссе " * 100,
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 800,
            "total_tokens": 1100,
            "cost_usd": 0.01,
        }

        # Проверка всегда возвращает низкую уникальность
        mock_check = CheckResult(
            uniqueness=30.0, system="textru",
            is_sufficient=False, required=70.0, text_length=1000,
        )

        mock_rewrite_result = RewriteResult(
            text="Перефразированный текст.",
            iterations=1,
            input_tokens=200,
            output_tokens=180,
            total_tokens=380,
            cost_usd=0.002,
        )

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_gen_response), \
             patch("src.generator.router.check_uniqueness", new_callable=AsyncMock, return_value=mock_check), \
             patch("src.generator.router.rewrite_for_uniqueness", new_callable=AsyncMock, return_value=mock_rewrite_result) as mock_rewrite:
            from src.generator.router import generate_and_check
            result, check = await generate_and_check(
                work_type="Эссе",
                title="Тест",
                required_uniqueness=70,
                antiplagiat_system="textru",
            )

        # 1 начальная проверка + 3 рерайта
        assert mock_rewrite.call_count == 3

    @pytest.mark.asyncio
    async def test_generate_and_check_sufficient(self):
        """Если уникальность сразу достаточна — рерайт не вызывается."""
        mock_gen_response = {
            "content": "Текст эссе " * 100,
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 800,
            "total_tokens": 1100,
            "cost_usd": 0.01,
        }

        mock_check = CheckResult(
            uniqueness=85.0, system="textru",
            is_sufficient=True, required=50.0, text_length=1000,
        )

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_gen_response), \
             patch("src.generator.router.check_uniqueness", new_callable=AsyncMock, return_value=mock_check), \
             patch("src.generator.router.rewrite_for_uniqueness", new_callable=AsyncMock) as mock_rewrite:
            from src.generator.router import generate_and_check
            result, check = await generate_and_check(
                work_type="Эссе",
                title="Тест",
                required_uniqueness=50,
            )

        assert result is not None
        assert check is not None
        assert check.is_sufficient is True
        assert check.uniqueness == 85.0
        mock_rewrite.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_check_unsupported_type(self):
        """Неподдерживаемый тип → (None, None)."""
        from src.generator.router import generate_and_check
        result, check = await generate_and_check(
            work_type="Курсовая работа",
            title="Тест",
        )
        assert result is None
        assert check is None

    @pytest.mark.asyncio
    async def test_generate_and_check_rewrite_succeeds(self):
        """Рерайт успешно повышает уникальность."""
        mock_gen_response = {
            "content": "Текст эссе " * 100,
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 800,
            "total_tokens": 1100,
            "cost_usd": 0.01,
        }

        # Первая проверка — низкая уникальность, вторая — достаточная
        check_results = [
            CheckResult(uniqueness=35.0, system="textru", is_sufficient=False, required=60.0, text_length=1000),
            CheckResult(uniqueness=72.0, system="textru", is_sufficient=True, required=60.0, text_length=1000),
        ]

        mock_rewrite_result = RewriteResult(
            text="Перефразированный текст.",
            iterations=1,
            input_tokens=200,
            output_tokens=180,
            total_tokens=380,
            cost_usd=0.002,
        )

        with patch("src.generator.essay.chat_completion", new_callable=AsyncMock, return_value=mock_gen_response), \
             patch("src.generator.router.check_uniqueness", new_callable=AsyncMock, side_effect=check_results), \
             patch("src.generator.router.rewrite_for_uniqueness", new_callable=AsyncMock, return_value=mock_rewrite_result) as mock_rewrite:
            from src.generator.router import generate_and_check
            result, check = await generate_and_check(
                work_type="Эссе",
                title="Тест",
                required_uniqueness=60,
            )

        assert result is not None
        assert check is not None
        assert check.is_sufficient is True
        assert check.uniqueness == 72.0
        # Только 1 рерайт понадобился
        assert mock_rewrite.call_count == 1
        # Токены от рерайта добавились к результату
        assert result.input_tokens == 300 + 200
        assert result.cost_usd == 0.01 + 0.002
