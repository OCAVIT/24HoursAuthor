"""Check uniqueness of generated text via text.ru API."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.antiplagiat.checker import check_uniqueness


async def main():
    text = Path("e:/VibeProjects/Autor24/scripts/test_gen_output.txt").read_text(encoding="utf-8")
    print(f"Text: {len(text)} chars, ~{len(text)//1800} pages")
    print("Checking via text.ru (sampling 3 fragments first)...")

    result = await check_uniqueness(
        text=text,
        system="textru",
        required_uniqueness=80.0,
    )

    print(f"\nResult:")
    print(f"  Uniqueness: {result.uniqueness:.1f}%")
    print(f"  Required:   {result.required:.1f}%")
    print(f"  Sufficient: {result.is_sufficient}")
    print(f"  Sampled:    {result.is_sampled}")
    print(f"  System:     {result.system}")


if __name__ == "__main__":
    asyncio.run(main())
