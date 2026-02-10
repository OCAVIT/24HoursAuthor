"""Generate DOCX from test_gen_output.txt using the docgen pipeline."""
import asyncio
from pathlib import Path

# Allow running from project root
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.docgen import build_docx


async def main():
    text_path = Path(__file__).resolve().parent / "test_gen_output.txt"
    out_path = Path(__file__).resolve().parent.parent / "tmp" / "test_gen" / "coursework.docx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = text_path.read_text(encoding="utf-8")
    print(f"Input: {len(text)} chars, ~{len(text)//1800} pages")

    result = await build_docx(
        title="Использование дифференцированного подхода в обучении младших школьников",
        text=text,
        work_type="Курсовая работа",
        subject="Педагогика",
        font_size=14,
        line_spacing=1.5,
        output_path=str(out_path),
    )

    if result:
        size_kb = result.stat().st_size / 1024
        print(f"OK: {result}  ({size_kb:.0f} KB)")
    else:
        print("FAILED")


if __name__ == "__main__":
    asyncio.run(main())
