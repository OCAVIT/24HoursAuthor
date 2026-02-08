"""Конфигурации языков программирования для песочницы."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class LanguageConfig:
    """Конфигурация языка программирования."""
    name: str
    extension: str
    compile_cmd: Optional[str]  # None если интерпретируемый
    run_cmd: str
    filename: str  # имя файла с исходником


# Поддерживаемые языки
LANGUAGES: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        name="Python",
        extension=".py",
        compile_cmd=None,
        run_cmd="python {file}",
        filename="main.py",
    ),
    "javascript": LanguageConfig(
        name="JavaScript",
        extension=".js",
        compile_cmd=None,
        run_cmd="node {file}",
        filename="main.js",
    ),
    "java": LanguageConfig(
        name="Java",
        extension=".java",
        compile_cmd="javac {file}",
        run_cmd="java -cp {dir} Main",
        filename="Main.java",
    ),
    "cpp": LanguageConfig(
        name="C++",
        extension=".cpp",
        compile_cmd="g++ -o {dir}/a.out {file}",
        run_cmd="{dir}/a.out",
        filename="main.cpp",
    ),
    "csharp": LanguageConfig(
        name="C#",
        extension=".cs",
        compile_cmd="csc -out:{dir}/main.exe {file}",
        run_cmd="mono {dir}/main.exe",
        filename="Main.cs",
    ),
}


def get_language(name: str) -> Optional[LanguageConfig]:
    """Получить конфигурацию языка по имени."""
    return LANGUAGES.get(name.lower())


def supported_languages() -> list[str]:
    """Список поддерживаемых языков."""
    return list(LANGUAGES.keys())


def detect_language(code: str, hint: str = "") -> str:
    """Определить язык по подсказке или коду.

    Args:
        code: Исходный код.
        hint: Подсказка (например, из описания заказа).

    Returns:
        Название языка (ключ из LANGUAGES) или 'python' по умолчанию.
    """
    hint_lower = hint.lower()

    # Прямое совпадение по подсказке
    for lang_key in LANGUAGES:
        if lang_key in hint_lower:
            return lang_key

    # Синонимы
    aliases = {
        "python": ["python", "питон", "py"],
        "javascript": ["javascript", "js", "node", "nodejs", "node.js"],
        "java": ["java", "джава"],
        "cpp": ["c++", "cpp", "си++", "с++"],
        "csharp": ["c#", "csharp", "си шарп", "c sharp", ".net"],
    }

    for lang_key, names in aliases.items():
        for name in names:
            if name in hint_lower:
                return lang_key

    # Определение по коду
    if "def " in code or "import " in code or "print(" in code:
        return "python"
    if "console.log" in code or "function " in code or "const " in code:
        return "javascript"
    if "public static void main" in code or "System.out" in code:
        return "java"
    if "#include" in code or "cout" in code or "std::" in code:
        return "cpp"
    if "Console.Write" in code or "namespace " in code or "using System" in code:
        return "csharp"

    return "python"
