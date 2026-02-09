"""
Pipeline test: scrape orders → find essay → score (GPT-4o-mini) → generate (GPT-4o) → DOCX.
Prints token usage and cost summary.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper.browser import browser_manager
from src.scraper.auth import login
from src.scraper.orders import fetch_order_list
from src.scraper.order_detail import fetch_order_detail, OrderDetail
from src.analyzer.order_scorer import score_order
from src.generator.essay import generate as generate_essay
from src.docgen.builder import build_docx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("pipeline_test")

# Типы работ, считающиеся эссе
ESSAY_TYPES = {"эссе", "сочинение", "аннотация", "творческая работа"}


def safe(text):
    if not text:
        return ""
    return str(text).encode("ascii", "replace").decode("ascii")


async def find_essay_order(page) -> OrderDetail:
    """Parse orders and find one that is an essay. If none found, create mock."""
    logger.info("=== Parsing orders to find an essay ===")
    orders = await fetch_order_list(page, max_pages=2)
    logger.info("Total orders: %d", len(orders))

    # Search for essay type
    for order in orders:
        wt = order.work_type.lower().strip()
        if any(t in wt for t in ESSAY_TYPES):
            logger.info("Found essay order: ID=%s, title='%s'", order.order_id, safe(order.title))
            detail_url = order.url or f"/order/getoneorder/{order.order_id}"
            detail = await fetch_order_detail(page, detail_url)
            return detail

    # No essay found — create a mock OrderDetail from a real order
    logger.info("No essay found in feed. Using first order as mock essay.")
    if orders:
        first = orders[0]
        detail_url = first.url or f"/order/getoneorder/{first.order_id}"
        detail = await fetch_order_detail(page, detail_url)
        # Override work type to essay for testing
        detail.work_type = "Эссе"
        if not detail.description:
            detail.description = detail.title
        return detail

    # Absolute fallback — fully mock
    logger.info("No orders at all. Creating fully mock essay order.")
    return OrderDetail(
        order_id="mock-001",
        title="Роль менеджмента в современном бизнесе",
        url="",
        work_type="Эссе",
        subject="Менеджмент",
        description="Раскрыть роль менеджмента в условиях цифровой трансформации бизнеса. "
                    "Рассмотреть основные функции менеджмента, привести примеры из практики.",
        pages_min=5,
        pages_max=7,
        font_size=14,
        line_spacing=1.5,
        required_uniqueness=50,
    )


async def main():
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0

    try:
        # === Step 1: Login ===
        logger.info("=== STEP 1: LOGIN ===")
        page = await login()
        logger.info("Login OK")

        # === Step 2: Find essay order ===
        logger.info("=== STEP 2: FIND ESSAY ORDER ===")
        order = await find_essay_order(page)

        print("\n" + "=" * 70)
        print("  ORDER FOR PIPELINE TEST")
        print(f"    ID:          {order.order_id}")
        print(f"    Title:       {safe(order.title)}")
        print(f"    Work type:   {safe(order.work_type)}")
        print(f"    Subject:     {safe(order.subject)}")
        print(f"    Pages:       {order.pages_min}-{order.pages_max}")
        print(f"    Description: {safe(order.description[:200])}")
        print("=" * 70)

        # === Step 3: Score order (GPT-4o-mini) ===
        logger.info("=== STEP 3: SCORING (GPT-4o-mini) ===")
        score_result = await score_order(order)

        total_input_tokens += score_result.input_tokens
        total_output_tokens += score_result.output_tokens
        total_cost_usd += score_result.cost_usd

        print("\n" + "=" * 70)
        print("  SCORING RESULT")
        print(f"    Score:          {score_result.score}/100")
        print(f"    Can do:         {score_result.can_do}")
        print(f"    Est. time:      {score_result.estimated_time_min} min")
        print(f"    Est. cost:      {score_result.estimated_cost_rub} RUB")
        print(f"    Reason:         {safe(score_result.reason)}")
        print(f"    Tokens:         {score_result.input_tokens} in / {score_result.output_tokens} out")
        print(f"    Cost:           ${score_result.cost_usd:.4f}")
        print("=" * 70)

        # === Step 4: Generate essay (GPT-4o) ===
        logger.info("=== STEP 4: GENERATE ESSAY (GPT-4o) ===")
        pages = order.pages_min or 5
        gen_result = await generate_essay(
            title=order.title,
            description=order.description,
            subject=order.subject,
            pages=pages,
            required_uniqueness=order.required_uniqueness,
            font_size=order.font_size,
            line_spacing=order.line_spacing,
        )

        total_input_tokens += gen_result.input_tokens
        total_output_tokens += gen_result.output_tokens
        total_cost_usd += gen_result.cost_usd

        print("\n" + "=" * 70)
        print("  GENERATION RESULT")
        print(f"    Pages approx:   ~{gen_result.pages_approx}")
        print(f"    Text length:    {len(gen_result.text)} chars")
        print(f"    Tokens:         {gen_result.input_tokens} in / {gen_result.output_tokens} out")
        print(f"    Cost:           ${gen_result.cost_usd:.4f}")
        print(f"    Preview:")
        preview = safe(gen_result.text[:500])
        for line in preview.split("\n")[:8]:
            print(f"      {line}")
        print("      ...")
        print("=" * 70)

        # === Step 5: Build DOCX ===
        logger.info("=== STEP 5: BUILD DOCX ===")
        docx_path = await build_docx(
            title=order.title,
            text=gen_result.text,
            work_type="Эссе",
            subject=order.subject,
            font_size=order.font_size,
            line_spacing=order.line_spacing,
        )

        print("\n" + "=" * 70)
        print("  DOCX RESULT")
        if docx_path and docx_path.exists():
            size_kb = docx_path.stat().st_size / 1024
            print(f"    File:           {docx_path}")
            print(f"    Size:           {size_kb:.1f} KB")
        else:
            print("    ERROR: DOCX not generated!")
        print("=" * 70)

        # === Summary ===
        print("\n" + "=" * 70)
        print("  TOTAL TOKEN USAGE & COST")
        print(f"    Input tokens:   {total_input_tokens:,}")
        print(f"    Output tokens:  {total_output_tokens:,}")
        print(f"    Total tokens:   {total_input_tokens + total_output_tokens:,}")
        print(f"    Total cost:     ${total_cost_usd:.4f}")
        print(f"    Cost in RUB:    ~{total_cost_usd * 90:.1f} RUB (at 90 RUB/USD)")
        print("=" * 70)

        logger.info("=== PIPELINE TEST COMPLETED SUCCESSFULLY ===")

    except Exception as e:
        logger.error("Pipeline test failed: %s", e, exc_info=True)
    finally:
        await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
