"""
Integration test: use actual scraper modules (auth, orders, order_detail).
Logs in, parses orders, opens one detail page, prints all fields.
"""
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper.browser import browser_manager
from src.scraper.auth import login
from src.scraper.orders import parse_order_cards, fetch_order_list
from src.scraper.order_detail import fetch_order_detail

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("integration_test")


def safe(text):
    """Safely encode text for console output."""
    if not text:
        return ""
    return str(text).encode("ascii", "replace").decode("ascii")


async def main():
    page = None
    try:
        # === Step 1: Login ===
        logger.info("=== STEP 1: LOGIN ===")
        page = await login()
        logger.info("Login successful! Page URL: %s", page.url)

        # === Step 2: Parse orders (first page only) ===
        logger.info("=== STEP 2: PARSE ORDERS ===")
        orders = await fetch_order_list(page, max_pages=1)
        logger.info("Total orders parsed: %d", len(orders))

        if not orders:
            logger.error("No orders found!")
            return

        # Print first 5 orders
        print("\n" + "=" * 80)
        for i, order in enumerate(orders[:5]):
            print(f"  ORDER #{i + 1}")
            print(f"    ID:          {order.order_id}")
            print(f"    Title:       {safe(order.title)}")
            print(f"    URL:         {order.url}")
            print(f"    Work type:   {safe(order.work_type)}")
            print(f"    Subject:     {safe(order.subject)}")
            print(f"    Deadline:    {safe(order.deadline)}")
            print(f"    Budget:      {safe(order.budget)}")
            print(f"    Budget RUB:  {order.budget_rub}")
            print(f"    Bids:        {order.bid_count}")
            print(f"    Files:       {safe(order.files_info)}")
            print(f"    Created:     {safe(order.creation_time)}")
            print(f"    Customer:    {safe(order.customer_online)}")
            print(f"    Badges:      {[safe(b) for b in order.customer_badges]}")
            print(f"    Description: {safe(order.description_preview[:150])}")
            print("-" * 80)

        # === Step 3: Open first order detail ===
        first = orders[0]
        logger.info("=== STEP 3: ORDER DETAIL (ID=%s) ===", first.order_id)

        detail_url = first.url
        if not detail_url:
            detail_url = f"/order/getoneorder/{first.order_id}"

        detail = await fetch_order_detail(page, detail_url)

        print("\n" + "=" * 80)
        print("  ORDER DETAIL")
        print(f"    ID:             {detail.order_id}")
        print(f"    Title:          {safe(detail.title)}")
        print(f"    URL:            {detail.url}")
        print(f"    Work type:      {safe(detail.work_type)}")
        print(f"    Subject:        {safe(detail.subject)}")
        print(f"    Deadline:       {safe(detail.deadline)}")
        print(f"    Pages min:      {detail.pages_min}")
        print(f"    Pages max:      {detail.pages_max}")
        print(f"    Font size:      {detail.font_size}")
        print(f"    Line spacing:   {detail.line_spacing}")
        print(f"    Uniqueness:     {detail.required_uniqueness}")
        print(f"    Antiplagiat:    {safe(detail.antiplagiat_system)}")
        print(f"    Budget:         {safe(detail.budget)}")
        print(f"    Budget RUB:     {detail.budget_rub}")
        print(f"    Average bid:    {detail.average_bid}")
        print(f"    Customer:       {safe(detail.customer_name)}")
        print(f"    Online:         {safe(detail.customer_online)}")
        print(f"    Badges:         {[safe(b) for b in detail.customer_badges]}")
        print(f"    Created:        {safe(detail.creation_time)}")
        print(f"    Files:          {[safe(f) for f in detail.file_names]}")
        print(f"    Description:    {safe(detail.description[:500])}")
        print("=" * 80)

        logger.info("=== ALL STEPS COMPLETED SUCCESSFULLY ===")

    except Exception as e:
        logger.error("Integration test failed: %s", e, exc_info=True)
    finally:
        await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
