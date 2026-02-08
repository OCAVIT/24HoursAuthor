"""Песочница для выполнения кода в изолированном процессе."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.sandbox.languages import get_language, LanguageConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30  # секунд
DEFAULT_MEMORY_MB = 256


@dataclass
class ExecutionResult:
    """Результат выполнения кода."""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    language: str
    timed_out: bool = False


async def execute_code(
    code: str,
    language: str,
    stdin: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> ExecutionResult:
    """Запустить код в изолированном процессе.

    Args:
        code: Исходный код для выполнения.
        language: Язык программирования (python, javascript, java, cpp, csharp).
        stdin: Входные данные для stdin.
        timeout: Таймаут выполнения в секундах.

    Returns:
        ExecutionResult с результатами выполнения.
    """
    lang_config = get_language(language)
    if lang_config is None:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=f"Язык '{language}' не поддерживается.",
            exit_code=-1,
            language=language,
        )

    # Создаём временную директорию для кода
    with tempfile.TemporaryDirectory(prefix="sandbox_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        code_file = tmpdir_path / lang_config.filename

        # Записываем код в файл
        code_file.write_text(code, encoding="utf-8")

        try:
            # Компиляция (если нужна)
            if lang_config.compile_cmd:
                compile_result = await _run_process(
                    cmd=_format_cmd(lang_config.compile_cmd, code_file, tmpdir_path),
                    cwd=tmpdir,
                    stdin_data="",
                    timeout=timeout,
                )
                if compile_result.exit_code != 0:
                    return ExecutionResult(
                        success=False,
                        stdout=compile_result.stdout,
                        stderr=compile_result.stderr,
                        exit_code=compile_result.exit_code,
                        language=language,
                    )

            # Запуск
            run_result = await _run_process(
                cmd=_format_cmd(lang_config.run_cmd, code_file, tmpdir_path),
                cwd=tmpdir,
                stdin_data=stdin,
                timeout=timeout,
            )

            return ExecutionResult(
                success=run_result.exit_code == 0,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                exit_code=run_result.exit_code,
                language=language,
                timed_out=run_result.timed_out,
            )

        except Exception as e:
            logger.error("Ошибка выполнения кода (%s): %s", language, e)
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                language=language,
            )


@dataclass
class _ProcessResult:
    """Внутренний результат запуска процесса."""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


async def _run_process(
    cmd: str,
    cwd: str,
    stdin_data: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> _ProcessResult:
    """Запустить процесс с таймаутом."""
    logger.debug("Запуск: %s (cwd=%s, timeout=%ds)", cmd, cwd, timeout)

    kwargs = {
        "cwd": cwd,
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }

    # На Windows создаём новую группу процессов для корректного kill
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    process = await asyncio.create_subprocess_shell(cmd, **kwargs)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(input=stdin_data.encode("utf-8") if stdin_data else None),
            timeout=timeout,
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        # Ограничиваем вывод (защита от бесконечного вывода)
        max_output = 50_000
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + "\n... (вывод обрезан)"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + "\n... (вывод обрезан)"

        return _ProcessResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode or 0,
        )

    except asyncio.TimeoutError:
        # Убиваем процесс и всех потомков при таймауте
        await _kill_process_tree(process)

        return _ProcessResult(
            stdout="",
            stderr=f"Превышен таймаут выполнения ({timeout} секунд).",
            exit_code=-1,
            timed_out=True,
        )


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Убить процесс и всё дерево дочерних процессов."""
    pid = process.pid
    if pid is None:
        return

    try:
        if os.name == "nt":
            # На Windows: taskkill /T убивает всё дерево процессов
            kill_proc = await asyncio.create_subprocess_shell(
                f"taskkill /F /T /PID {pid}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        else:
            # На Linux: отправляем SIGKILL группе процессов
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    try:
        await process.wait()
    except Exception:
        pass


def _format_cmd(template: str, code_file: Path, tmpdir: Path) -> str:
    """Подставить пути в шаблон команды."""
    result = template.format(
        file=str(code_file),
        dir=str(tmpdir),
    )

    # Для Python используем sys.executable (надёжнее чем 'python' в PATH)
    if result.startswith("python "):
        result = f'"{sys.executable}" {result[7:]}'

    return result
