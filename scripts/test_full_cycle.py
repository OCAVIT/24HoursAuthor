"""Полный тест реального цикла: поиск заказа OCAVIT -> скоринг -> ставка -> БД -> отчёт."""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scraper.browser import browser_manager
from src.scraper.auth import login
from src.scraper.orders import fetch_order_list, parse_order_cards
from src.scraper.order_detail import fetch_order_detail
from src.analyzer.order_scorer import score_order
from src.analyzer.price_calculator import calculate_price
from src.ai_client import chat_completion
from src.config import settings
from src.database.models import Base
from src.database.connection import engine, async_session
from src.database import crud

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TARGET_CUSTOMER = "OCAVIT"

# ---- Вспомогательные функции ----


def safe(text):
    """Безопасный вывод кириллицы на Windows-консоль."""
    if not text:
        return ""
    return str(text).encode("ascii", "replace").decode("ascii")


async def init_db():
    """Создать таблицы если не существуют."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB tables created/verified")


async def generate_bid_comment(title: str, work_type: str, subject: str) -> str:
    """Сгенерировать комментарий к ставке через GPT-4o-mini."""
    result = await chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты автор на Автор24. Напиши короткий комментарий к ставке (2-3 предложения). "
                    "Дружелюбно, уверенно, упомяни опыт в теме. "
                    "НЕ упоминай AI, нейросети, ChatGPT. Пиши как живой человек."
                ),
            },
            {
                "role": "user",
                "content": f"Тема: {title}\nТип работы: {work_type}\nПредмет: {subject}",
            },
        ],
        model=settings.openai_model_fast,
        temperature=0.7,
        max_tokens=150,
    )
    return result["content"].strip()


async def probe_bid_form(page):
    """Зондировать структуру формы ставки на текущей странице."""
    probe = await page.evaluate("""
        () => {
            let result = {inputs: [], textareas: [], buttons: [], forms: []};

            // Все input
            document.querySelectorAll('input').forEach(el => {
                result.inputs.push({
                    type: el.type,
                    name: el.name,
                    placeholder: el.placeholder,
                    id: el.id,
                    className: el.className.substring(0, 80),
                    visible: el.offsetParent !== null
                });
            });

            // Все textarea
            document.querySelectorAll('textarea').forEach(el => {
                result.textareas.push({
                    name: el.name,
                    placeholder: el.placeholder,
                    id: el.id,
                    className: el.className.substring(0, 80),
                    visible: el.offsetParent !== null
                });
            });

            // Все button
            document.querySelectorAll('button').forEach(el => {
                result.buttons.push({
                    text: el.textContent.trim().substring(0, 50),
                    type: el.type,
                    className: el.className.substring(0, 80),
                    visible: el.offsetParent !== null
                });
            });

            // Формы
            document.querySelectorAll('form').forEach(el => {
                result.forms.push({
                    action: el.action,
                    method: el.method,
                    id: el.id,
                    className: el.className.substring(0, 80)
                });
            });

            return result;
        }
    """)
    return probe


async def place_bid_adaptive(page, order_url: str, price: int, comment: str) -> bool:
    """Адаптивная постановка ставки с зондированием формы."""
    # Переходим на страницу заказа
    current = page.url
    if order_url not in current:
        await page.goto(order_url, wait_until="domcontentloaded", timeout=60000)
        await browser_manager.short_delay()
        await page.wait_for_selector('[class*="AuctionDetailsStyled"], [class*="OrderStyled"]', timeout=15000)
        await browser_manager.short_delay()

    # Зондируем форму
    probe = await probe_bid_form(page)

    # Сохраняем зондирование для отладки
    os.makedirs("tmp/probe", exist_ok=True)
    with open("tmp/probe/bid_form_probe.json", "w", encoding="utf-8") as f:
        json.dump(probe, f, ensure_ascii=False, indent=2)
    logger.info("Bid form probe saved to tmp/probe/bid_form_probe.json")

    # Скриншот формы
    await page.screenshot(path="tmp/probe/bid_form_before.png", full_page=True)

    # Поиск поля ввода цены (реальный селектор: #MakeOffer__inputBid)
    price_selectors = [
        '#MakeOffer__inputBid',
        'input[id*="inputBid"]',
        'input[name="price"]',
        'input[name="bid_price"]',
        'input[placeholder*="цен"]',
        'input[placeholder*="ставк"]',
        'input[type="number"]',
    ]

    price_filled = False
    for sel in price_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.fill(str(price))
                price_filled = True
                logger.info("Price filled via selector: %s", sel)
                break
        except Exception:
            continue

    if not price_filled:
        logger.error("Could not find price input field!")
        logger.info("Available inputs: %s", json.dumps(probe.get("inputs", []), ensure_ascii=False, indent=2))
        return False

    await browser_manager.short_delay()

    # Поиск поля комментария (реальный селектор: #makeOffer_comment)
    comment_selectors = [
        '#makeOffer_comment',
        'textarea[id*="comment"]',
        'textarea[placeholder*="приветствен"]',
        'textarea[placeholder*="сообщен"]',
        'textarea[placeholder*="коммент"]',
        'textarea',
    ]

    for sel in comment_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.fill(comment)
                logger.info("Comment filled via selector: %s", sel)
                break
        except Exception:
            continue

    await browser_manager.short_delay()

    # Поиск кнопки отправки (реальный текст: "Поставить ставку")
    submit_selectors = [
        'button:has-text("Поставить ставку")',
        'button:has-text("Предложить")',
        'button:has-text("Отправить")',
        'button:has-text("Откликнуться")',
    ]

    submit_clicked = False
    for sel in submit_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                submit_clicked = True
                logger.info("Submit clicked via selector: %s", sel)
                break
        except Exception:
            continue

    if not submit_clicked:
        logger.error("Could not find submit button!")
        logger.info("Available buttons: %s", json.dumps(probe.get("buttons", []), ensure_ascii=False, indent=2))
        return False

    # Ждём результата
    await page.wait_for_load_state("domcontentloaded", timeout=10000)
    await browser_manager.short_delay()

    # Скриншот после ставки
    await page.screenshot(path="tmp/probe/bid_form_after.png", full_page=True)

    logger.info("Bid %d RUB submitted!", price)
    return True


async def verify_bid(page, order_url: str, our_price: int) -> bool:
    """Проверить что наша ставка отображается на странице заказа."""
    await page.goto(order_url, wait_until="domcontentloaded", timeout=60000)
    await browser_manager.short_delay()

    try:
        await page.wait_for_selector(
            '[class*="AuctionDetailsStyled"], [class*="OrderStyled"]',
            timeout=15000,
        )
        await browser_manager.short_delay()
    except Exception:
        pass

    # Ищем нашу ставку на странице
    page_text = await page.evaluate("() => document.body.innerText")
    price_str = str(our_price)

    if price_str in page_text:
        logger.info("Bid verification: our price %s found on page!", price_str)
        return True

    # Также проверяем есть ли блок "Ваша ставка" или "Вы уже откликнулись"
    indicators = ["ваша ставка", "вы уже откликнулись", "вы откликнулись", "ваш отклик"]
    page_lower = page_text.lower()
    for ind in indicators:
        if ind in page_lower:
            logger.info("Bid verification: found indicator '%s'", ind)
            return True

    logger.warning("Bid verification: could not confirm bid on page")
    return False


# ---- Основной цикл ----


async def main():
    report = []
    total_api_cost = 0.0
    total_tokens = 0

    try:
        # 0. Инициализация БД
        await init_db()
        report.append("[OK] DB initialized")

        # 1. Логин
        logger.info("=== STEP 1: Login ===")
        page = await login()
        report.append("[OK] Logged in to avtor24.ru")

        # 2. Парсинг ленты заказов (3 страницы)
        logger.info("=== STEP 2: Fetching orders ===")
        all_orders = await fetch_order_list(page, max_pages=3)
        report.append(f"[OK] Parsed {len(all_orders)} orders from 3 pages")

        # 3. Поиск заказа от OCAVIT через Search Bar
        logger.info("=== STEP 3: Searching for %s via search bar ===", TARGET_CUSTOMER)

        target = None

        # Переходим на страницу поиска
        search_url = f"{settings.avtor24_base_url}/order/search"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await browser_manager.short_delay()
        try:
            await page.wait_for_selector(".auctionOrder", timeout=15000)
            await browser_manager.short_delay()
        except Exception:
            pass

        # Ищем search bar и вводим имя заказчика
        search_selectors = [
            'input[placeholder*="поиск" i]',
            'input[placeholder*="найти" i]',
            'input[placeholder*="search" i]',
            'input[name="search"]',
            'input[name="q"]',
            'input[name="text"]',
            'input[type="search"]',
            '[class*="SearchStyled"] input',
            '[class*="Search"] input',
            '[class*="search"] input',
        ]

        search_filled = False
        for sel in search_selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.fill(TARGET_CUSTOMER)
                    search_filled = True
                    logger.info("Search filled via: %s", sel)
                    break
            except Exception:
                continue

        if not search_filled:
            # Probe: dump all inputs to find the right one
            inputs = await page.evaluate("""
                () => Array.from(document.querySelectorAll('input')).map(el => ({
                    type: el.type, name: el.name, placeholder: el.placeholder,
                    className: el.className.substring(0, 100),
                    visible: el.offsetParent !== null,
                    id: el.id
                }))
            """)
            logger.info("Could not find search bar. Available inputs:")
            for inp in inputs:
                if inp.get("visible"):
                    logger.info("  %s", json.dumps(inp, ensure_ascii=False))

            report.append("[WARN] Search bar not found, falling back to first order")
        else:
            # Нажимаем Enter или кнопку поиска
            await page.keyboard.press("Enter")
            await browser_manager.short_delay()

            # Ждём результатов
            try:
                await page.wait_for_selector(".auctionOrder", timeout=15000)
                await browser_manager.short_delay()
            except Exception:
                pass

            # Сохраняем скриншот результатов поиска
            os.makedirs("tmp/probe", exist_ok=True)
            await page.screenshot(path="tmp/probe/search_results.png", full_page=False)

            # Парсим результаты поиска
            search_results = await parse_order_cards(page)
            logger.info("Search returned %d results for '%s'", len(search_results), TARGET_CUSTOMER)
            report.append(f"[INFO] Search for '{TARGET_CUSTOMER}' returned {len(search_results)} results")

            if search_results:
                target = search_results[0]
                report.append(f"[OK] Using first search result: {target.title} (ID: {target.order_id})")
            else:
                report.append(f"[INFO] No results for '{TARGET_CUSTOMER}' in search bar")

        # Если поиск не дал результатов — используем первый заказ из ленты
        if target is None:
            if all_orders:
                fallback = None
                for o in all_orders:
                    if o.bid_count <= 5:
                        fallback = o
                        break
                if fallback is None:
                    fallback = all_orders[0]
                target = fallback
                report.append(f"[INFO] Falling back to order: {target.title} (ID: {target.order_id})")
            else:
                report.append("[FAIL] No orders found at all!")
                print("\n" + "=" * 60)
                for line in report:
                    print(safe(line))
                print("=" * 60)
                await browser_manager.close()
                return

        report.append(f"[OK] Target order: {target.title} (ID: {target.order_id})")

        # 4. Парсинг деталей заказа
        logger.info("=== STEP 4: Fetching order details ===")
        detail = await fetch_order_detail(page, target.url)

        report.append("")
        report.append("=== ORDER DETAILS ===")
        report.append(f"Title:        {detail.title}")
        report.append(f"Type:         {detail.work_type}")
        report.append(f"Subject:      {detail.subject}")
        report.append(f"Pages:        {detail.pages_min}-{detail.pages_max}")
        report.append(f"Budget:       {detail.budget} ({detail.budget_rub} RUB)")
        report.append(f"Deadline:     {detail.deadline}")
        report.append(f"Uniqueness:   {detail.required_uniqueness}%")
        report.append(f"Antiplagiat:  {detail.antiplagiat_system}")
        report.append(f"Customer:     {detail.customer_name}")
        report.append(f"Avg bid:      {detail.average_bid} RUB")
        report.append(f"Files:        {detail.file_names}")
        report.append(f"Description:  {detail.description[:200]}...")

        # 5. Скоринг через GPT-4o-mini
        logger.info("=== STEP 5: Scoring order ===")
        score = await score_order(detail)
        total_api_cost += score.cost_usd
        total_tokens += score.input_tokens + score.output_tokens

        report.append("")
        report.append("=== SCORING ===")
        report.append(f"Score:        {score.score}/100")
        report.append(f"Can do:       {score.can_do}")
        report.append(f"Reason:       {score.reason}")
        report.append(f"Est. time:    {score.estimated_time_min} min")
        report.append(f"Est. cost:    {score.estimated_cost_rub} RUB")
        report.append(f"API cost:     ${score.cost_usd:.4f}")

        # 6. Расчёт цены ставки
        logger.info("=== STEP 6: Calculating bid price ===")
        bid_price = calculate_price(detail)

        # Ставим минимальную цену (как в задании)
        bid_price = max(300, min(bid_price, 500))
        report.append("")
        report.append("=== BID PRICE ===")
        report.append(f"Bid price:    {bid_price} RUB")

        # 7. Генерация комментария к ставке
        logger.info("=== STEP 7: Generating bid comment ===")
        comment = await generate_bid_comment(detail.title, detail.work_type, detail.subject)
        report.append(f"Comment:      {comment}")

        # 8. Постановка реальной ставки
        logger.info("=== STEP 8: Placing bid ===")
        bid_success = await place_bid_adaptive(page, detail.url, bid_price, comment)

        if bid_success:
            report.append("[OK] Bid placed successfully!")
        else:
            report.append("[WARN] Bid placement may have failed - check screenshots")

        # 9. Верификация ставки
        logger.info("=== STEP 9: Verifying bid ===")
        bid_verified = await verify_bid(page, detail.url, bid_price)
        if bid_verified:
            report.append("[OK] Bid verified on order page")
        else:
            report.append("[WARN] Could not verify bid on page")

        # 10. Сохранение в БД
        logger.info("=== STEP 10: Saving to DB ===")
        async with async_session() as session:
            # Проверяем, нет ли уже этого заказа
            existing = await crud.get_order_by_avtor24_id(session, detail.order_id)
            if existing:
                logger.info("Order already in DB, updating...")
                order_obj = await crud.update_order_status(
                    session, existing.id, "bid_placed",
                    bid_price=bid_price,
                    bid_comment=comment,
                    bid_placed_at=datetime.utcnow(),
                    score=score.score,
                    api_cost_usd=total_api_cost,
                )
                db_order_id = existing.id
            else:
                order_obj = await crud.create_order(
                    session,
                    avtor24_id=detail.order_id,
                    title=detail.title,
                    work_type=detail.work_type,
                    subject=detail.subject,
                    description=detail.description,
                    pages_min=detail.pages_min,
                    pages_max=detail.pages_max,
                    font_size=detail.font_size,
                    line_spacing=detail.line_spacing,
                    required_uniqueness=detail.required_uniqueness,
                    antiplagiat_system=detail.antiplagiat_system,
                    budget_rub=detail.budget_rub,
                    bid_price=bid_price,
                    bid_comment=comment,
                    bid_placed_at=datetime.utcnow(),
                    score=score.score,
                    status="bid_placed",
                    customer_username=detail.customer_name,
                    api_cost_usd=total_api_cost,
                )
                db_order_id = order_obj.id

            # Лог действия
            await crud.create_action_log(
                session,
                action="bid",
                details=f"Bid {bid_price} RUB on order {detail.order_id} ({detail.title})",
                order_id=db_order_id,
            )

            # API usage
            await crud.track_api_usage(
                session,
                model=settings.openai_model_fast,
                purpose="scoring",
                input_tokens=score.input_tokens,
                output_tokens=score.output_tokens,
                cost_usd=score.cost_usd,
                order_id=db_order_id,
            )

            # 11. Уведомление
            await crud.create_notification(
                session,
                type="new_order",
                title=f"Ставка на: {detail.title}",
                body={
                    "order_id": detail.order_id,
                    "title": detail.title,
                    "work_type": detail.work_type,
                    "budget": detail.budget_rub,
                    "bid_price": bid_price,
                    "score": score.score,
                    "can_do": score.can_do,
                },
                order_id=db_order_id,
            )

            report.append(f"[OK] Saved to DB (id={db_order_id})")
            report.append("[OK] Notification created")
            report.append("[OK] Action log created")
            report.append("[OK] API usage tracked")

        # 12. Итоговый отчёт
        report.append("")
        report.append("=== SUMMARY ===")
        report.append(f"Order:        {detail.title} (ID: {detail.order_id})")
        report.append(f"Bid placed:   {bid_price} RUB")
        report.append(f"Score:        {score.score}/100")
        report.append(f"Tokens used:  {total_tokens}")
        report.append(f"API cost:     ${total_api_cost:.4f}")
        report.append(f"Status:       {'SUCCESS' if bid_success else 'PARTIAL'}")

    except Exception as e:
        report.append(f"[ERROR] {type(e).__name__}: {e}")
        logger.exception("Test failed")

    finally:
        await browser_manager.close()

    # Вывод
    print("\n" + "=" * 60)
    for line in report:
        print(safe(line))
    print("=" * 60)

    # Сохраняем
    os.makedirs("tmp/probe", exist_ok=True)
    with open("tmp/probe/full_cycle_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\nReport saved to tmp/probe/full_cycle_report.txt")


if __name__ == "__main__":
    asyncio.run(main())
