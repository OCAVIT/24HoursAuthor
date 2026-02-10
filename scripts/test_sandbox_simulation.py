"""Симуляция работы Docker-песочницы — реальные задачи по программированию.

Тестирует полный цикл: GPT-4o генерирует код -> sandbox запускает -> проверяем результат.
Расход API: ~$0.05-0.10 (GPT-4o для генерации кода).
"""

import asyncio
import os
import sys

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sandbox.executor import execute_code
from src.sandbox.languages import supported_languages, detect_language
from src.generator.code_task import generate


# --- Тест 1: Прямой запуск кода в песочнице (без GPT) ---
DIRECT_TESTS = [
    {
        "name": "Python: Hello World",
        "language": "python",
        "code": 'print("Hello, World!")',
        "expected_stdout": "Hello, World!",
        "should_succeed": True,
    },
    {
        "name": "Python: FizzBuzz (1-20)",
        "language": "python",
        "code": """for i in range(1, 21):
    if i % 15 == 0:
        print("FizzBuzz")
    elif i % 3 == 0:
        print("Fizz")
    elif i % 5 == 0:
        print("Buzz")
    else:
        print(i)""",
        "expected_stdout": "1",  # partial match
        "should_succeed": True,
    },
    {
        "name": "Python: stdin input",
        "language": "python",
        "code": """n = int(input())
print(sum(range(1, n + 1)))""",
        "stdin": "10",
        "expected_stdout": "55",
        "should_succeed": True,
    },
    {
        "name": "Python: syntax error",
        "language": "python",
        "code": "print('hello'\nprint('world')",
        "expected_stdout": "",
        "should_succeed": False,
    },
    {
        "name": "Python: timeout (infinite loop)",
        "language": "python",
        "code": "while True: pass",
        "expected_stdout": "",
        "should_succeed": False,
        "timeout": 3,
    },
    {
        "name": "JavaScript: array operations",
        "language": "javascript",
        "code": """const arr = [5, 3, 8, 1, 9, 2];
arr.sort((a, b) => a - b);
console.log(arr.join(', '));
console.log('Sum:', arr.reduce((s, x) => s + x, 0));""",
        "expected_stdout": "1, 2, 3, 5, 8, 9",
        "should_succeed": True,
    },
]

# --- Тест 2: Полный цикл GPT + sandbox ---
GPT_TASKS = [
    {
        "name": "Числа Фибоначчи",
        "title": "Написать функцию вычисления n-го числа Фибоначчи",
        "description": "Написать программу, которая принимает число n (от stdin) и выводит n-е число Фибоначчи. Пример: n=10 -> 55",
        "subject": "Python",
        "check": lambda result: "55" in result.text,
    },
    {
        "name": "Сортировка пузырьком",
        "title": "Реализовать сортировку пузырьком",
        "description": "Написать функцию bubble_sort, которая сортирует список чисел. Продемонстрировать на примере [64, 34, 25, 12, 22, 11, 90]. Вывести отсортированный список.",
        "subject": "Python, алгоритмы",
        "check": lambda result: "11" in result.text and "90" in result.text,
    },
    {
        "name": "Факториал рекурсивно",
        "title": "Вычислить факториал числа рекурсивно",
        "description": "Написать рекурсивную функцию factorial(n), вывести factorial(10). Результат: 3628800.",
        "subject": "Python",
        "check": lambda result: "3628800" in result.text,
    },
]


async def run_direct_tests():
    """Прямые тесты песочницы (без GPT)."""
    print("\n" + "=" * 70)
    print("  ЧАСТЬ 1: ПРЯМЫЕ ТЕСТЫ ПЕСОЧНИЦЫ (без GPT)")
    print("=" * 70)

    passed = 0
    failed = 0

    for test in DIRECT_TESTS:
        name = test["name"]
        print(f"\n--- {name} ---")

        result = await execute_code(
            code=test["code"],
            language=test["language"],
            stdin=test.get("stdin", ""),
            timeout=test.get("timeout", 10),
        )

        ok = result.success == test["should_succeed"]
        if test["expected_stdout"] and test["should_succeed"]:
            ok = ok and test["expected_stdout"] in result.stdout

        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"  Success: {result.success} (expected: {test['should_succeed']})")
        if result.stdout:
            stdout_preview = result.stdout[:100].replace("\n", " | ")
            print(f"  Stdout: {stdout_preview}")
        if result.stderr:
            stderr_preview = result.stderr[:100].replace("\n", " | ")
            print(f"  Stderr: {stderr_preview}")
        if result.timed_out:
            print(f"  Timeout: yes")
        print(f"  [{status}]")

    print(f"\n  Прямые тесты: {passed} OK, {failed} FAIL из {len(DIRECT_TESTS)}")
    return passed, failed


async def run_gpt_tests():
    """Полный цикл: GPT генерирует код -> sandbox запускает."""
    print("\n" + "=" * 70)
    print("  ЧАСТЬ 2: ПОЛНЫЙ ЦИКЛ (GPT-4o + ПЕСОЧНИЦА)")
    print("=" * 70)

    passed = 0
    failed = 0
    total_cost = 0.0
    total_tokens = 0

    for task in GPT_TASKS:
        name = task["name"]
        print(f"\n--- {name} ---")
        print(f"  Задача: {task['title']}")

        try:
            result = await generate(
                title=task["title"],
                description=task["description"],
                subject=task["subject"],
            )

            total_cost += result.cost_usd
            total_tokens += result.total_tokens

            exec_ok = result.execution_result and result.execution_result.success
            check_ok = task["check"](result) if exec_ok else False

            print(f"  Язык: {result.language}")
            print(f"  Попыток: {result.attempts}")
            print(f"  Код выполнился: {exec_ok}")

            if result.execution_result:
                if result.execution_result.stdout:
                    stdout_preview = result.execution_result.stdout[:150].replace("\n", " | ")
                    print(f"  Вывод: {stdout_preview}")
                if result.execution_result.stderr:
                    stderr_preview = result.execution_result.stderr[:100].replace("\n", " | ")
                    print(f"  Ошибка: {stderr_preview}")

            print(f"  Проверка вывода: {check_ok}")
            print(f"  [{result.total_tokens} tok, ${result.cost_usd:.4f}]")

            if result.fix_history:
                print(f"  История исправлений:")
                for fix in result.fix_history:
                    print(f"    - {fix[:80]}")

            if exec_ok and check_ok:
                print(f"  [OK]")
                passed += 1
            else:
                print(f"  [FAIL]")
                failed += 1

                # Показать сгенерированный код при провале
                code_preview = result.text[:300]
                print(f"  Код:\n{code_preview}")

        except Exception as e:
            print(f"  [ERROR] {e}")
            failed += 1

    print(f"\n  GPT тесты: {passed} OK, {failed} FAIL из {len(GPT_TASKS)}")
    print(f"  Токенов: {total_tokens:,}, Стоимость: ${total_cost:.4f}")
    return passed, failed, total_cost, total_tokens


async def main():
    print("=" * 70)
    print("  СИМУЛЯЦИЯ DOCKER-ПЕСОЧНИЦЫ")
    print(f"  Поддерживаемые языки: {', '.join(supported_languages())}")
    print("=" * 70)

    # Часть 1: прямые тесты
    d_passed, d_failed = await run_direct_tests()

    # Часть 2: GPT + sandbox
    g_passed, g_failed, cost, tokens = await run_gpt_tests()

    # Итоги
    total_passed = d_passed + g_passed
    total_failed = d_failed + g_failed
    total = total_passed + total_failed

    print("\n" + "=" * 70)
    print("  ИТОГИ")
    print("=" * 70)
    print(f"  Всего тестов:   {total}")
    print(f"  Пройдено:       {total_passed}")
    print(f"  Провалено:      {total_failed}")
    print(f"  API токенов:    {tokens:,}")
    print(f"  API стоимость:  ${cost:.4f}")

    if total_failed == 0:
        print("\n  Все тесты пройдены!")
    else:
        print(f"\n  {total_failed} тест(ов) провалено.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
