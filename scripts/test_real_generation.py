"""Тестовый запуск реальной генерации через API — проверка пайплайна и библиографии."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.generator.referat import generate as referat_generate
from src.generator.stepwise import CHARS_PER_PAGE


async def main():
    result = await referat_generate(
        title="Искусственный интеллект в образовании",
        subject="Информатика",
        description="Роль AI в современном образовании, перспективы и риски",
        pages=3,
    )

    report = []
    report.append("=" * 60)
    report.append("РЕЗУЛЬТАТ ГЕНЕРАЦИИ")
    report.append("=" * 60)
    report.append(f"Тип работы: {result.work_type}")
    report.append(f"Страниц (прибл.): {result.pages_approx}")
    report.append(f"Символов: {len(result.text)}")
    report.append(f"Целевой минимум: {3 * CHARS_PER_PAGE} символов")
    report.append(f"Объём достигнут: {'ДА' if len(result.text) >= 3 * CHARS_PER_PAGE else 'НЕТ'}")
    report.append(f"Токены: {result.total_tokens} (in={result.input_tokens}, out={result.output_tokens})")
    report.append(f"Стоимость: ${result.cost_usd:.4f}")

    if result.plan:
        report.append(f"\nПлан ({len(result.plan.chapters)} глав):")
        for ch in result.plan.chapters:
            report.append(f"  - {ch['title']}")

    report.append("\n" + "=" * 60)
    report.append("ПОЛНЫЙ ТЕКСТ:")
    report.append("=" * 60)
    report.append(result.text)

    out = "\n".join(report)
    with open("scripts/test_output.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print("Done. Output saved to scripts/test_output.txt")


if __name__ == "__main__":
    asyncio.run(main())
