"""Тесты песочницы: выполнение кода, таймаут, AI-цикл исправления."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.sandbox.executor import execute_code, ExecutionResult
from src.sandbox.languages import (
    get_language, supported_languages, detect_language, LanguageConfig,
)
from src.generator.code_task import (
    generate as code_task_generate,
    GenerationResult as CodeResult,
    _clean_code, _build_prompt, _format_output,
)


# ===== Тесты языковых конфигураций =====

class TestLanguages:
    """Тесты конфигураций языков."""

    def test_get_python(self):
        """Python конфигурация доступна."""
        cfg = get_language("python")
        assert cfg is not None
        assert cfg.name == "Python"
        assert cfg.extension == ".py"
        assert cfg.compile_cmd is None

    def test_get_javascript(self):
        """JavaScript конфигурация доступна."""
        cfg = get_language("javascript")
        assert cfg is not None
        assert cfg.name == "JavaScript"
        assert cfg.extension == ".js"

    def test_get_java(self):
        """Java конфигурация доступна."""
        cfg = get_language("java")
        assert cfg is not None
        assert cfg.compile_cmd is not None

    def test_get_cpp(self):
        """C++ конфигурация доступна."""
        cfg = get_language("cpp")
        assert cfg is not None
        assert cfg.compile_cmd is not None

    def test_get_csharp(self):
        """C# конфигурация доступна."""
        cfg = get_language("csharp")
        assert cfg is not None

    def test_get_unknown(self):
        """Неизвестный язык → None."""
        assert get_language("brainfuck") is None

    def test_supported_languages(self):
        """Поддерживается минимум 5 языков."""
        langs = supported_languages()
        assert len(langs) >= 5
        assert "python" in langs
        assert "javascript" in langs
        assert "java" in langs
        assert "cpp" in langs
        assert "csharp" in langs

    def test_detect_python_by_hint(self):
        """Определение Python по подсказке."""
        assert detect_language("", "напиши на python") == "python"
        assert detect_language("", "Задача на питон") == "python"

    def test_detect_javascript_by_hint(self):
        """Определение JavaScript по подсказке."""
        assert detect_language("", "решение на javascript") == "javascript"
        assert detect_language("", "написать на node.js") == "javascript"

    def test_detect_java_by_hint(self):
        """Определение Java по подсказке."""
        assert detect_language("", "программа на java") == "java"

    def test_detect_cpp_by_hint(self):
        """Определение C++ по подсказке."""
        assert detect_language("", "задача на C++") == "cpp"

    def test_detect_csharp_by_hint(self):
        """Определение C# по подсказке."""
        assert detect_language("", "программа на C#") == "csharp"

    def test_detect_python_by_code(self):
        """Определение Python по коду."""
        code = "def main():\n    print('hello')"
        assert detect_language(code, "") == "python"

    def test_detect_javascript_by_code(self):
        """Определение JavaScript по коду."""
        code = "console.log('hello');"
        assert detect_language(code, "") == "javascript"

    def test_detect_java_by_code(self):
        """Определение Java по коду."""
        code = "public static void main(String[] args) { System.out.println(); }"
        assert detect_language(code, "") == "java"

    def test_detect_cpp_by_code(self):
        """Определение C++ по коду."""
        code = "#include <iostream>\nint main() { std::cout << 42; }"
        assert detect_language(code, "") == "cpp"

    def test_detect_default_python(self):
        """Без подсказок → Python по умолчанию."""
        assert detect_language("x = 42", "") == "python"


# ===== Тесты executor =====

class TestExecutor:
    """Тесты выполнения кода."""

    @pytest.mark.asyncio
    async def test_python_execution(self):
        """Python print('hello') → stdout='hello'."""
        result = await execute_code(
            code='print("hello")',
            language="python",
            timeout=10,
        )

        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.stdout.strip() == "hello"
        assert result.exit_code == 0
        assert result.language == "python"

    @pytest.mark.asyncio
    async def test_python_error(self):
        """Синтаксическая ошибка Python → stderr содержит traceback."""
        result = await execute_code(
            code='print("hello',  # незакрытая кавычка
            language="python",
            timeout=10,
        )

        assert result.success is False
        assert result.exit_code != 0
        assert "SyntaxError" in result.stderr or "EOL" in result.stderr

    @pytest.mark.asyncio
    async def test_python_with_stdin(self):
        """Python с stdin."""
        code = "name = input()\nprint(f'Hello, {name}!')"
        result = await execute_code(
            code=code,
            language="python",
            stdin="World",
            timeout=10,
        )

        assert result.success is True
        assert "Hello, World!" in result.stdout

    @pytest.mark.asyncio
    async def test_python_runtime_error(self):
        """Python runtime error → stderr содержит traceback."""
        result = await execute_code(
            code="x = 1 / 0",
            language="python",
            timeout=10,
        )

        assert result.success is False
        assert "ZeroDivisionError" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Бесконечный цикл → таймаут."""
        result = await execute_code(
            code="while True: pass",
            language="python",
            timeout=2,  # короткий таймаут для быстрого теста
        )

        assert result.success is False
        assert result.timed_out is True
        assert "таймаут" in result.stderr.lower() or "timeout" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_unsupported_language(self):
        """Неподдерживаемый язык → ошибка."""
        result = await execute_code(
            code="print 42",
            language="brainfuck",
            timeout=10,
        )

        assert result.success is False
        assert "не поддерживается" in result.stderr

    @pytest.mark.asyncio
    async def test_python_multiline(self):
        """Многострочный Python-код."""
        code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

for i in range(1, 6):
    print(f"{i}! = {factorial(i)}")
"""
        result = await execute_code(code=code, language="python", timeout=10)

        assert result.success is True
        assert "5! = 120" in result.stdout

    @pytest.mark.asyncio
    async def test_python_empty_output(self):
        """Python код без вывода → пустой stdout."""
        result = await execute_code(
            code="x = 42",
            language="python",
            timeout=10,
        )

        assert result.success is True
        assert result.stdout == ""


# ===== Тесты code_task генератора =====

class TestCodeTaskGenerator:
    """Тесты генератора задач по программированию."""

    @pytest.mark.asyncio
    async def test_generate_success_first_try(self):
        """Код генерируется и выполняется с первой попытки."""
        mock_ai_response = {
            "content": 'print("Hello, World!")',
            "model": "gpt-4o",
            "input_tokens": 200,
            "output_tokens": 50,
            "total_tokens": 250,
            "cost_usd": 0.001,
        }

        mock_exec = ExecutionResult(
            success=True,
            stdout="Hello, World!",
            stderr="",
            exit_code=0,
            language="python",
        )

        with patch("src.generator.code_task.chat_completion", new_callable=AsyncMock, return_value=mock_ai_response), \
             patch("src.generator.code_task.execute_code", new_callable=AsyncMock, return_value=mock_exec):
            result = await code_task_generate(
                title="Вывести Hello World",
                description="Напиши программу, которая выводит Hello, World!",
                subject="Python",
            )

        assert isinstance(result, CodeResult)
        assert result.work_type == "Задача по программированию"
        assert result.language == "python"
        assert result.attempts == 1
        assert result.total_tokens == 250
        assert result.cost_usd == 0.001
        assert result.execution_result is not None
        assert result.execution_result.success is True

    @pytest.mark.asyncio
    async def test_generate_fix_on_second_try(self):
        """Код с ошибкой → исправление со второй попытки."""
        # Первый ответ — код с ошибкой
        first_response = {
            "content": 'print("hello"',  # ошибка
            "model": "gpt-4o",
            "input_tokens": 200,
            "output_tokens": 50,
            "total_tokens": 250,
            "cost_usd": 0.001,
        }
        # Второй ответ — исправленный код
        second_response = {
            "content": 'print("hello")',
            "model": "gpt-4o",
            "input_tokens": 300,
            "output_tokens": 50,
            "total_tokens": 350,
            "cost_usd": 0.002,
        }

        exec_fail = ExecutionResult(
            success=False, stdout="", stderr="SyntaxError: unexpected EOF",
            exit_code=1, language="python",
        )
        exec_success = ExecutionResult(
            success=True, stdout="hello", stderr="",
            exit_code=0, language="python",
        )

        mock_ai = AsyncMock(side_effect=[first_response, second_response])
        mock_exec = AsyncMock(side_effect=[exec_fail, exec_success])

        with patch("src.generator.code_task.chat_completion", mock_ai), \
             patch("src.generator.code_task.execute_code", mock_exec):
            result = await code_task_generate(
                title="Hello World",
                subject="Python",
            )

        assert result.execution_result.success is True
        assert result.attempts == 2
        assert result.total_tokens == 600  # 250 + 350
        assert len(result.fix_history) == 1

    @pytest.mark.asyncio
    async def test_generate_all_attempts_fail(self):
        """5 попыток безуспешны — возвращается последний результат."""
        mock_response = {
            "content": "broken code",
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": 0.001,
        }

        exec_fail = ExecutionResult(
            success=False, stdout="", stderr="Error",
            exit_code=1, language="python",
        )

        with patch("src.generator.code_task.chat_completion", new_callable=AsyncMock, return_value=mock_response), \
             patch("src.generator.code_task.execute_code", new_callable=AsyncMock, return_value=exec_fail):
            result = await code_task_generate(
                title="Сложная задача",
                subject="Python",
            )

        assert result.execution_result.success is False
        assert result.attempts == 5
        assert len(result.fix_history) == 5
        assert result.total_tokens == 750  # 150 * 5

    @pytest.mark.asyncio
    async def test_language_detection_from_subject(self):
        """Язык определяется из предмета/описания."""
        mock_response = {
            "content": 'console.log("hi");',
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": 0.001,
        }

        mock_exec = ExecutionResult(
            success=True, stdout="hi", stderr="",
            exit_code=0, language="javascript",
        )

        with patch("src.generator.code_task.chat_completion", new_callable=AsyncMock, return_value=mock_response), \
             patch("src.generator.code_task.execute_code", new_callable=AsyncMock, return_value=mock_exec):
            result = await code_task_generate(
                title="Задача на JavaScript",
                description="Написать скрипт на Node.js",
                subject="Программирование",
            )

        assert result.language == "javascript"

    @pytest.mark.asyncio
    async def test_context_included_in_prompt(self):
        """Описание задачи попадает в промпт."""
        mock_response = {
            "content": 'print(42)',
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": 0.001,
        }
        mock_exec = ExecutionResult(
            success=True, stdout="42", stderr="",
            exit_code=0, language="python",
        )

        with patch("src.generator.code_task.chat_completion", new_callable=AsyncMock, return_value=mock_response) as mock_ai, \
             patch("src.generator.code_task.execute_code", new_callable=AsyncMock, return_value=mock_exec):
            await code_task_generate(
                title="Числа Фибоначчи",
                description="Вывести первые 10 чисел Фибоначчи",
            )

        # Проверяем что описание попало в промпт
        call_args = mock_ai.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        user_msg = messages[-1]["content"]
        assert "Фибоначчи" in user_msg


# ===== Тесты вспомогательных функций =====

class TestHelpers:
    """Тесты утилит code_task."""

    def test_clean_code_plain(self):
        """Обычный код не меняется."""
        code = 'print("hello")'
        assert _clean_code(code) == code

    def test_clean_code_markdown_python(self):
        """Markdown-обёртка ```python убирается."""
        raw = '```python\nprint("hello")\n```'
        assert _clean_code(raw) == 'print("hello")'

    def test_clean_code_markdown_generic(self):
        """Markdown-обёртка ``` убирается."""
        raw = '```\nprint("hello")\n```'
        assert _clean_code(raw) == 'print("hello")'

    def test_clean_code_whitespace(self):
        """Лишние пробелы убираются."""
        assert _clean_code("  print('hi')  ") == "print('hi')"

    def test_build_prompt_basic(self):
        """Промпт содержит название задачи и язык."""
        prompt = _build_prompt(
            title="Сортировка массива",
            description="Отсортировать массив пузырьком",
            subject="Алгоритмы",
            language="python",
            methodology_summary=None,
        )
        assert "Сортировка массива" in prompt
        assert "Python" in prompt
        assert "пузырьком" in prompt

    def test_build_prompt_with_methodology(self):
        """Промпт включает методичку."""
        prompt = _build_prompt(
            title="Тест",
            description="",
            subject="",
            language="python",
            methodology_summary="Использовать рекурсию",
        )
        assert "рекурсию" in prompt

    def test_format_output_success(self):
        """Форматирование при успешном выполнении."""
        exec_result = ExecutionResult(
            success=True, stdout="42", stderr="",
            exit_code=0, language="python",
        )
        output = _format_output('print(42)', "python", exec_result)
        assert "print(42)" in output
        assert "42" in output
        assert "Python" in output

    def test_format_output_error(self):
        """Форматирование при ошибке."""
        exec_result = ExecutionResult(
            success=False, stdout="", stderr="NameError: name 'x' is not defined",
            exit_code=1, language="python",
        )
        output = _format_output('print(x)', "python", exec_result)
        assert "ошибки" in output.lower()


# ===== Тесты роутера (код) =====

class TestRouterCodeTask:
    """Тесты интеграции code_task с роутером."""

    def test_code_task_supported(self):
        """Задачи по программированию теперь поддерживаются."""
        from src.generator.router import is_supported
        assert is_supported("Задача по программированию") is True

    def test_code_task_in_supported_types(self):
        """Задачи по программированию в списке поддерживаемых."""
        from src.generator.router import supported_types
        assert "Задача по программированию" in supported_types()

    def test_code_task_generator_not_none(self):
        """Генератор для задач по программированию — не None."""
        from src.generator.router import get_generator
        gen = get_generator("Задача по программированию")
        assert gen is not None
        assert callable(gen)
