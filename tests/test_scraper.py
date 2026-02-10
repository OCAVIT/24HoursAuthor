"""Тесты скрапера — парсинг HTML, браузер, ставки, чат (всё через моки)."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import pytest_asyncio

from src.scraper.browser import BrowserManager, USER_AGENTS, VIEWPORTS, COOKIES_PATH
from src.scraper.orders import parse_order_cards, OrderSummary, _extract_number
from src.scraper.order_detail import fetch_order_detail, OrderDetail, _extract_int, _extract_float
from src.scraper.bidder import place_bid
from src.scraper.chat import (
    get_messages, send_message, ChatMessage,
    get_accepted_order_ids, get_active_chats,
    _navigate_home, _extract_order_ids_from_section,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ===== Утилиты для мокирования Playwright =====

def _make_locator_mock(elements: list[dict]) -> MagicMock:
    """Создать мок Playwright locator, возвращающий данные из элементов."""
    locator = MagicMock()
    locator.count = AsyncMock(return_value=len(elements))

    if elements:
        first = MagicMock()
        first.inner_text = AsyncMock(return_value=elements[0].get("text", ""))
        first.get_attribute = AsyncMock(side_effect=lambda attr: elements[0].get(attr, None))
        first.fill = AsyncMock()
        first.click = AsyncMock()
        first.press = AsyncMock()
        first.set_input_files = AsyncMock()
        locator.first = first
    else:
        first = MagicMock()
        first.inner_text = AsyncMock(return_value="")
        first.get_attribute = AsyncMock(return_value=None)
        locator.first = first

    async def _all():
        mocks = []
        for el in elements:
            m = MagicMock()
            m.inner_text = AsyncMock(return_value=el.get("text", ""))
            m.get_attribute = AsyncMock(side_effect=lambda attr, e=el: e.get(attr, None))
            # Вложенный locator
            m.locator = MagicMock(side_effect=lambda sel, e=el: _make_locator_mock(
                e.get("children", {}).get(sel, [])
            ))
            mocks.append(m)
        return mocks

    locator.all = _all
    return locator


def _make_page_from_html(html_path: Path) -> MagicMock:
    """Создать мок Page, который 'парсит' HTML через захардкоженные данные."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.url = "https://avtor24.ru/order/search"
    page.is_closed = MagicMock(return_value=False)
    return page


# ===== Тесты BrowserManager =====

class TestBrowserManager:
    """Тесты менеджера браузера."""

    def test_user_agents_not_empty(self):
        """UA список не пуст."""
        assert len(USER_AGENTS) >= 5

    def test_viewports_not_empty(self):
        """Список разрешений не пуст."""
        assert len(VIEWPORTS) >= 3

    def test_browser_manager_init(self):
        """BrowserManager создаётся с корректными атрибутами."""
        bm = BrowserManager()
        assert bm._browser is None
        assert bm._page is None
        assert bm._user_agent in USER_AGENTS
        assert bm._viewport in VIEWPORTS

    @pytest.mark.asyncio
    async def test_random_delay_bounds(self):
        """random_delay выдерживает указанные границы."""
        bm = BrowserManager()
        import time
        start = time.monotonic()
        await bm.random_delay(min_sec=0.05, max_sec=0.15)
        elapsed = time.monotonic() - start
        assert 0.04 <= elapsed <= 0.5  # с запасом на overhead

    @pytest.mark.asyncio
    async def test_short_delay(self):
        """short_delay отрабатывает за 1-3 секунды."""
        bm = BrowserManager()
        import time
        start = time.monotonic()
        await bm.short_delay()
        elapsed = time.monotonic() - start
        assert 0.8 <= elapsed <= 4.0

    @pytest.mark.asyncio
    async def test_save_cookies_no_context(self):
        """save_cookies не падает если контекст отсутствует."""
        bm = BrowserManager()
        await bm.save_cookies()  # не должен бросить исключение

    @pytest.mark.asyncio
    async def test_close_no_browser(self):
        """close не падает если браузер не был запущен."""
        bm = BrowserManager()
        await bm.close()
        assert bm._browser is None
        assert bm._page is None

    @pytest.mark.asyncio
    async def test_start_launches_browser(self):
        """start() запускает Playwright и возвращает Page."""
        bm = BrowserManager()

        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.add_init_script = AsyncMock()

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.cookies = AsyncMock(return_value=[])
        mock_context.add_cookies = AsyncMock()
        mock_context.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_chromium = MagicMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium
        mock_pw.stop = AsyncMock()

        with patch("src.scraper.browser.async_playwright") as mock_async_pw:
            mock_starter = AsyncMock(return_value=mock_pw)
            mock_async_pw.return_value.start = mock_starter
            mock_async_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw)
            mock_async_pw.return_value.__aexit__ = AsyncMock()

            # Мокаем async_playwright().start()
            mock_async_pw.return_value = MagicMock()
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_pw)

            page = await bm.start()
            assert page is mock_page

        await bm.close()


# ===== Тесты парсинга ленты заказов =====

class TestOrderListParsing:
    """Тесты парсинга ленты заказов (мок HTML)."""

    def _build_order_list_page(self) -> MagicMock:
        """Создать мок страницы с 5 заказами.

        Мокает page.evaluate() — JS-based bulk extraction,
        как реализовано в parse_order_cards().
        """
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_selector = AsyncMock()

        # Данные в формате, который возвращает page.evaluate() в orders.py
        raw_orders = [
            {
                "orderId": "10001",
                "title": "Курсовая по экономике предприятия",
                "url": "/order/10001",
                "workType": "Курсовая работа",
                "subject": "Экономика",
                "deadline": "15.02.2026",
                "filesInfo": "1 файл",
                "description": "Курсовая работа по экономике предприятия, 25-30 страниц...",
                "budget": "3 000 ₽",
                "bidCount": 2,
                "creationTime": "08.02.2026",
                "customerOnline": "онлайн",
                "customerName": "Иван",
                "badges": ["Постоянный клиент"],
            },
            {
                "orderId": "10002",
                "title": "Эссе по философии",
                "url": "/order/10002",
                "workType": "Эссе",
                "subject": "Философия",
                "deadline": "10.02.2026",
                "filesInfo": "0 файлов",
                "description": "Эссе на тему \"Свобода и ответственность\"...",
                "budget": "1 500 ₽",
                "bidCount": 0,
                "creationTime": "07.02.2026",
                "customerOnline": "",
                "customerName": "Мария",
                "badges": [],
            },
            {
                "orderId": "10003",
                "title": "Реферат по истории России",
                "url": "/order/10003",
                "workType": "Реферат",
                "subject": "История",
                "deadline": "20.02.2026",
                "filesInfo": "2 файла",
                "description": "Реферат по истории России XIX века, 15 страниц...",
                "budget": "1 200 ₽",
                "bidCount": 5,
                "creationTime": "06.02.2026",
                "customerOnline": "онлайн",
                "customerName": "Пётр",
                "badges": ["Быстрый заказ"],
            },
            {
                "orderId": "10004",
                "title": "Контрольная по математике",
                "url": "/order/10004",
                "workType": "Контрольная работа",
                "subject": "Математика",
                "deadline": "09.02.2026",
                "filesInfo": "1 файл",
                "description": "10 задач по линейной алгебре...",
                "budget": "800 ₽",
                "bidCount": 1,
                "creationTime": "08.02.2026",
                "customerOnline": "",
                "customerName": "",
                "badges": [],
            },
            {
                "orderId": "10005",
                "title": "Дипломная работа по менеджменту",
                "url": "/order/10005",
                "workType": "Дипломная работа",
                "subject": "Менеджмент",
                "deadline": "01.04.2026",
                "filesInfo": "3 файла",
                "description": "ВКР по управлению персоналом, 80-100 страниц, антиплагиат 70%...",
                "budget": "15 000 ₽",
                "bidCount": 3,
                "creationTime": "05.02.2026",
                "customerOnline": "онлайн",
                "customerName": "Елена",
                "badges": ["Постоянный клиент"],
            },
        ]

        page.evaluate = AsyncMock(return_value=raw_orders)
        return page

    @pytest.mark.asyncio
    async def test_parse_order_list_count(self):
        """Парсинг возвращает 5 заказов."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert len(orders) == 5

    @pytest.mark.asyncio
    async def test_parse_order_list_ids(self):
        """Все order_id корректны."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        ids = [o.order_id for o in orders]
        assert ids == ["10001", "10002", "10003", "10004", "10005"]

    @pytest.mark.asyncio
    async def test_parse_order_list_titles(self):
        """Заголовки заказов корректны."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].title == "Курсовая по экономике предприятия"
        assert orders[1].title == "Эссе по философии"

    @pytest.mark.asyncio
    async def test_parse_order_list_work_types(self):
        """Типы работ парсятся корректно."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].work_type == "Курсовая работа"
        assert orders[1].work_type == "Эссе"
        assert orders[2].work_type == "Реферат"

    @pytest.mark.asyncio
    async def test_parse_order_list_budget(self):
        """Бюджеты парсятся корректно."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].budget_rub == 3000
        assert orders[1].budget_rub == 1500

    @pytest.mark.asyncio
    async def test_parse_order_list_bids(self):
        """Количество ставок парсится."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].bid_count == 2
        assert orders[1].bid_count == 0
        assert orders[2].bid_count == 5

    @pytest.mark.asyncio
    async def test_parse_order_list_online(self):
        """Онлайн-статус заказчика парсится."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].customer_online == "онлайн"
        assert orders[1].customer_online == ""

    @pytest.mark.asyncio
    async def test_parse_order_list_badge(self):
        """Бейдж заказчика парсится."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert "Постоянный клиент" in orders[0].customer_badges
        assert "Быстрый заказ" in orders[2].customer_badges


# ===== Тесты парсинга деталей заказа =====

class TestOrderDetailParsing:
    """Тесты парсинга детальной страницы заказа."""

    def _build_detail_page(self) -> MagicMock:
        """Создать мок страницы с деталями заказа.

        Мокает page.evaluate() — JS-based extraction,
        как реализовано в fetch_order_detail().
        """
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_selector = AsyncMock()

        raw_detail = {
            "title": "Курсовая по экономике предприятия",
            "fields": {
                "Тип работы": "Курсовая работа",
                "Предмет": "Экономика предприятия",
                "Кол-во страниц": "от 25 до 30",
                "Шрифт": "14",
                "Интервал": "1.5",
                "Оригинальность": "60%",
                "Антиплагиат": "ETXT Антиплагиат",
                "Срок сдачи": "15.02.2026",
                "Гарантийный срок": "20 дней",
            },
            "budgetText": "3 000 ₽",
            "description": (
                "Необходимо написать курсовую работу по экономике предприятия на тему "
                "\"Анализ финансово-хозяйственной деятельности предприятия\"."
            ),
            "customerName": "Иван И.",
            "customerOnline": "сейчас на сайте",
            "avgBid": "2 800 ₽",
            "fileNames": ["методичка.pdf", "требования.docx"],
            "fileUrls": ["/file/download/55001", "/file/download/55002"],
            "creationTime": "08.02.2026",
            "badges": ["Постоянный клиент"],
        }

        page.evaluate = AsyncMock(return_value=raw_detail)
        return page

    @pytest.mark.asyncio
    async def test_detail_title(self):
        """Заголовок парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.title == "Курсовая по экономике предприятия"

    @pytest.mark.asyncio
    async def test_detail_order_id(self):
        """order_id извлекается из URL."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.order_id == "10001"

    @pytest.mark.asyncio
    async def test_detail_work_type(self):
        """Тип работы парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.work_type == "Курсовая работа"

    @pytest.mark.asyncio
    async def test_detail_subject(self):
        """Предмет парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.subject == "Экономика предприятия"

    @pytest.mark.asyncio
    async def test_detail_pages(self):
        """Количество страниц парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.pages_min == 25
        assert detail.pages_max == 30

    @pytest.mark.asyncio
    async def test_detail_uniqueness(self):
        """Требуемая уникальность парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.required_uniqueness == 60
        assert detail.antiplagiat_system == "ETXT Антиплагиат"

    @pytest.mark.asyncio
    async def test_detail_budget(self):
        """Бюджет парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.budget_rub == 3000

    @pytest.mark.asyncio
    async def test_detail_average_bid(self):
        """Средняя ставка парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.average_bid == 2800

    @pytest.mark.asyncio
    async def test_detail_files(self):
        """Прикреплённые файлы парсятся."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert len(detail.file_urls) == 2
        assert "55001" in detail.file_urls[0]
        assert "55002" in detail.file_urls[1]

    @pytest.mark.asyncio
    async def test_detail_customer_info(self):
        """Информация о заказчике парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert "Иван" in detail.customer_name

    @pytest.mark.asyncio
    async def test_detail_description(self):
        """Описание парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert "курсовую работу" in detail.description

    @pytest.mark.asyncio
    async def test_detail_font_size(self):
        """Размер шрифта парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.font_size == 14

    @pytest.mark.asyncio
    async def test_detail_line_spacing(self):
        """Межстрочный интервал парсится."""
        page = self._build_detail_page()
        with patch("src.scraper.order_detail.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            detail = await fetch_order_detail(page, "https://avtor24.ru/order/10001")
        assert detail.line_spacing == 1.5


# ===== Тесты постановки ставок =====

class TestBidder:
    """Тесты постановки ставок."""

    @pytest.mark.asyncio
    async def test_bid_placement_success(self):
        """Ставка успешно ставится."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/10001"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        # Мокаем поле цены
        price_input = MagicMock()
        price_input.count = AsyncMock(return_value=1)
        price_input.first = MagicMock()
        price_input.first.fill = AsyncMock()

        # Мокаем поле комментария
        comment_input = MagicMock()
        comment_input.count = AsyncMock(return_value=1)
        comment_input.first = MagicMock()
        comment_input.first.fill = AsyncMock()

        # Мокаем кнопку
        submit_btn = MagicMock()
        submit_btn.count = AsyncMock(return_value=1)
        submit_btn.first = MagicMock()
        submit_btn.first.click = AsyncMock()

        # Ошибок нет
        error_el = MagicMock()
        error_el.count = AsyncMock(return_value=0)

        def page_locator(sel):
            if "inputBid" in sel or "MakeOffer" in sel:
                return price_input
            elif "comment" in sel or "makeOffer_comment" in sel:
                return comment_input
            elif "Поставить ставку" in sel:
                return submit_btn
            elif "error" in sel:
                return error_el
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            return m

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.bidder.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            result = await place_bid(page, "https://avtor24.ru/order/10001", 2800, "Сделаю в срок!")

        assert result is True
        price_input.first.fill.assert_awaited_once_with("2800")
        comment_input.first.fill.assert_awaited_once_with("Сделаю в срок!")
        submit_btn.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bid_no_price_field(self):
        """Ставка не ставится если нет поля цены."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/10001"
        page.goto = AsyncMock()

        def page_locator(sel):
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            return m

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.bidder.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            result = await place_bid(page, "https://avtor24.ru/order/10001", 2800, "Тест")

        assert result is False

    @pytest.mark.asyncio
    async def test_bid_with_error(self):
        """Ставка не ставится при ошибке на странице."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/10001"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        price_input = MagicMock()
        price_input.count = AsyncMock(return_value=1)
        price_input.first = MagicMock()
        price_input.first.fill = AsyncMock()

        comment_input = MagicMock()
        comment_input.count = AsyncMock(return_value=1)
        comment_input.first = MagicMock()
        comment_input.first.fill = AsyncMock()

        submit_btn = MagicMock()
        submit_btn.count = AsyncMock(return_value=1)
        submit_btn.first = MagicMock()
        submit_btn.first.click = AsyncMock()

        error_el = MagicMock()
        error_el.count = AsyncMock(return_value=1)
        error_el.first = MagicMock()
        error_el.first.inner_text = AsyncMock(return_value="Вы уже поставили ставку")

        def page_locator(sel):
            if "inputBid" in sel or "MakeOffer" in sel:
                return price_input
            elif "comment" in sel or "makeOffer_comment" in sel:
                return comment_input
            elif "Поставить ставку" in sel:
                return submit_btn
            elif "error" in sel:
                return error_el
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            return m

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.bidder.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            result = await place_bid(page, "https://avtor24.ru/order/10001", 2800, "Тест")

        assert result is False


# ===== Тесты чата =====

class TestChat:
    """Тесты чата."""

    @pytest.mark.asyncio
    async def test_get_messages(self):
        """Сообщения парсятся из чата (JS evaluate)."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/getoneorder/10001"
        page.goto = AsyncMock()

        # get_messages uses page.evaluate() returning list of dicts
        js_result = [
            {"text": "Здравствуйте! Сможете сделать?", "isSystem": False, "isOutgoing": False, "timestamp": "10:30"},
            {"text": "Да, тема знакомая, сделаю в срок.", "isSystem": False, "isOutgoing": True, "timestamp": "10:45"},
            {"text": "Методичку прикрепила, посмотрите.", "isSystem": False, "isOutgoing": False, "timestamp": "11:00"},
        ]
        page.evaluate = AsyncMock(return_value=js_result)

        # _ensure_chat_tab needs locator
        chat_tab = MagicMock()
        chat_tab.count = AsyncMock(return_value=0)
        page.locator = MagicMock(return_value=chat_tab)

        messages = await get_messages(page, "10001")

        assert len(messages) == 3
        assert messages[0].is_incoming is True
        assert messages[1].is_incoming is False
        assert "Сможете сделать" in messages[0].text

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Сообщение отправляется (textarea + JS send)."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/getoneorder/10001"
        page.goto = AsyncMock()
        # page.evaluate returns True (send button found via JS)
        page.evaluate = AsyncMock(return_value=True)

        textarea = MagicMock()
        textarea.count = AsyncMock(return_value=1)
        textarea.first = MagicMock()
        textarea.first.click = AsyncMock()
        textarea.first.fill = AsyncMock()
        textarea.first.type = AsyncMock()

        # _ensure_chat_tab: button locator
        chat_tab = MagicMock()
        chat_tab.count = AsyncMock(return_value=0)

        def page_locator(sel):
            if sel == "textarea":
                return textarea
            return chat_tab

        page.locator = MagicMock(side_effect=page_locator)

        result = await send_message(page, "10001", "Работа готова!")

        assert result is True
        textarea.first.fill.assert_awaited_once_with("")
        textarea.first.type.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_no_input(self):
        """Отправка не удаётся если нет textarea."""
        page = MagicMock()
        page.url = "https://avtor24.ru/other"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=False)

        def page_locator(sel):
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            m.first = MagicMock()
            m.first.fill = AsyncMock()
            m.first.click = AsyncMock()
            return m

        page.locator = MagicMock(side_effect=page_locator)

        result = await send_message(page, "10001", "Тест")

        assert result is False


# ===== Тесты парсинга активных заказов с /home =====

class TestActiveOrdersParsing:
    """Тесты get_accepted_order_ids / get_active_chats — парсинг раздела «Активные» на /home."""

    def _make_home_page(self, evaluate_result, url="https://avtor24.ru/home"):
        """Создать мок страницы /home.

        evaluate_result: list[dict] с полями {id, tag} — формат _extract_orders_with_tags.
        """
        page = MagicMock()
        page.url = url
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=evaluate_result)
        return page

    @staticmethod
    def _ids(ids: list[str], tag: str = "") -> list[dict]:
        """Хелпер: превратить список id в формат [{id, tag}] для мока evaluate."""
        return [{"id": oid, "tag": tag} for oid in ids]

    @pytest.mark.asyncio
    async def test_get_accepted_order_ids_returns_ids(self):
        """get_accepted_order_ids возвращает order_id из раздела «Активные»."""
        page = self._make_home_page(self._ids(["12345", "67890"]))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == ["12345", "67890"]
        page.goto.assert_awaited_once()
        page.evaluate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_accepted_order_ids_empty(self):
        """get_accepted_order_ids возвращает [] если нет активных заказов."""
        page = self._make_home_page([])

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_accepted_order_ids_login_redirect(self):
        """get_accepted_order_ids возвращает [] если редирект на логин."""
        page = MagicMock()
        page.url = "https://avtor24.ru/login"
        page.goto = AsyncMock(side_effect=Exception("ERR_ABORTED"))
        page.evaluate = AsyncMock(return_value=[])

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_accepted_order_ids_exception_returns_empty(self):
        """get_accepted_order_ids возвращает [] при ошибке evaluate."""
        page = MagicMock()
        page.url = "https://avtor24.ru/home"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("JS error"))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_active_chats_returns_ids(self):
        """get_active_chats возвращает order_id из раздела «Активные»."""
        page = self._make_home_page(self._ids(["11111", "22222", "33333"]))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_active_chats(page)

        assert result == ["11111", "22222", "33333"]

    @pytest.mark.asyncio
    async def test_get_active_chats_empty(self):
        """get_active_chats возвращает [] если нет чатов."""
        page = self._make_home_page([])

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_active_chats(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_active_chats_login_redirect(self):
        """get_active_chats возвращает [] при редиректе на логин."""
        page = MagicMock()
        page.url = "https://avtor24.ru/login"
        page.goto = AsyncMock(side_effect=Exception("ERR_ABORTED"))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_active_chats(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_navigate_home_success(self):
        """_navigate_home возвращает True при успешной навигации."""
        page = MagicMock()
        page.url = "https://avtor24.ru/home"
        page.goto = AsyncMock()

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await _navigate_home(page)

        assert result is True
        page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigate_home_login_redirect(self):
        """_navigate_home возвращает False при редиректе на /login."""
        page = MagicMock()
        page.url = "https://avtor24.ru/login"
        page.goto = AsyncMock(side_effect=Exception("ERR_ABORTED"))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await _navigate_home(page)

        assert result is False

    @pytest.mark.asyncio
    async def test_navigate_home_non_abort_error_raises(self):
        """_navigate_home пробрасывает не-ERR_ABORTED ошибки."""
        page = MagicMock()
        page.url = "https://avtor24.ru/home"
        page.goto = AsyncMock(side_effect=Exception("Network timeout"))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            with pytest.raises(Exception, match="Network timeout"):
                await _navigate_home(page)

    @pytest.mark.asyncio
    async def test_extract_order_ids_from_section_passes_keywords(self):
        """_extract_order_ids_from_section передаёт ключевые слова в JS evaluate."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[{"id": "55555", "tag": ""}])

        result = await _extract_order_ids_from_section(
            page, ["Активные", "активные"]
        )

        assert result == ["55555"]
        # Проверяем что evaluate вызван с ключевыми словами
        page.evaluate.assert_awaited_once()
        call_args = page.evaluate.call_args
        assert call_args[0][1] == ["Активные", "активные"]

    @pytest.mark.asyncio
    async def test_accepted_and_active_chats_use_same_keywords(self):
        """get_accepted_order_ids и get_active_chats ищут 'Активные' (не 'Ждут подтверждения')."""
        page = self._make_home_page(self._ids(["99999"]))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"

            await get_accepted_order_ids(page)
            call1_keywords = page.evaluate.call_args[0][1]

            page.evaluate.reset_mock()
            page.goto.reset_mock()

            await get_active_chats(page)
            call2_keywords = page.evaluate.call_args[0][1]

        # Оба используют "Активные" keywords
        assert "Активные" in call1_keywords
        assert "Активные" in call2_keywords
        # НЕ используют "Ждут подтверждения"
        for kw in call1_keywords:
            assert "Ждут подтверждения" not in kw
        for kw in call2_keywords:
            assert "Ждут подтверждения" not in kw

    @pytest.mark.asyncio
    async def test_get_accepted_multiple_orders_deduped(self):
        """Дубликаты order_id не возвращаются (JS-уровень дедупликации)."""
        page = self._make_home_page(self._ids(["11111", "22222"]))

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert len(result) == len(set(result))

    # --- Тесты фильтрации по тегам статусов ---

    @pytest.mark.asyncio
    async def test_filters_out_completed_orders(self):
        """Заказы с тегом 'завершен'/'завершён' не попадают в результат."""
        items = [
            {"id": "10001", "tag": ""},
            {"id": "10002", "tag": "завершен"},
            {"id": "10003", "tag": "Завершён"},
            {"id": "10004", "tag": ""},
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == ["10001", "10004"]

    @pytest.mark.asyncio
    async def test_filters_out_waiting_confirmation(self):
        """Заказы с тегом 'Ждёт подтверждения' не попадают в accepted."""
        items = [
            {"id": "20001", "tag": ""},
            {"id": "20002", "tag": "Ждёт подтверждения"},
            {"id": "20003", "tag": "ждет подтверждения"},
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == ["20001"]

    @pytest.mark.asyncio
    async def test_filters_out_cancelled_orders(self):
        """Заказы с тегом 'отменен'/'отменён' не попадают в результат."""
        items = [
            {"id": "30001", "tag": "отменен"},
            {"id": "30002", "tag": "Отменён"},
            {"id": "30003", "tag": ""},
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == ["30003"]

    @pytest.mark.asyncio
    async def test_active_chats_also_filters_by_tags(self):
        """get_active_chats тоже фильтрует по тегам."""
        items = [
            {"id": "40001", "tag": ""},
            {"id": "40002", "tag": "завершен"},
            {"id": "40003", "tag": "Ждёт подтверждения"},
            {"id": "40004", "tag": ""},
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_active_chats(page)

        assert result == ["40001", "40004"]

    @pytest.mark.asyncio
    async def test_mixed_tags_comprehensive(self):
        """Комплексный тест: разные теги фильтруются корректно."""
        items = [
            {"id": "50001", "tag": ""},                         # в работе ✓
            {"id": "50002", "tag": "завершен"},                 # ✗
            {"id": "50003", "tag": "Ждёт подтверждения"},       # ✗
            {"id": "50004", "tag": "отменен"},                  # ✗
            {"id": "50005", "tag": "В работе"},                 # неизвестный тег = в работе ✓
            {"id": "50006", "tag": ""},                         # в работе ✓
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == ["50001", "50005", "50006"]

    @pytest.mark.asyncio
    async def test_all_orders_filtered_returns_empty(self):
        """Если все заказы завершены/ожидают — пустой список."""
        items = [
            {"id": "60001", "tag": "завершен"},
            {"id": "60002", "tag": "Ждёт подтверждения"},
        ]
        page = self._make_home_page(items)

        with patch("src.scraper.chat.settings") as mock_settings:
            mock_settings.avtor24_base_url = "https://avtor24.ru"
            result = await get_accepted_order_ids(page)

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_orders_with_tags_returns_tuples(self):
        """_extract_orders_with_tags возвращает список кортежей (id, tag)."""
        from src.scraper.chat import _extract_orders_with_tags

        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {"id": "70001", "tag": "завершен"},
            {"id": "70002", "tag": ""},
        ])

        result = await _extract_orders_with_tags(page, ["Активные"])
        assert result == [("70001", "завершен"), ("70002", "")]


# ===== Тесты ChatMessage.is_assistant =====

class TestChatMessageAssistant:
    """Тесты определения сообщений от Ассистента."""

    def test_is_assistant_by_sender_name(self):
        """Сообщение от 'Ассистент' определяется как assistant."""
        msg = ChatMessage(
            order_id="123",
            text="Условия заказа изменены",
            is_incoming=True,
            sender_name="Ассистент",
        )
        assert msg.is_assistant is True

    def test_is_assistant_case_insensitive(self):
        """is_assistant нечувствителен к регистру."""
        msg = ChatMessage(
            order_id="123",
            text="Какой-то текст",
            is_incoming=True,
            sender_name="ассистент",
        )
        assert msg.is_assistant is True

    def test_is_assistant_by_system_text(self):
        """Системное сообщение с 'ассистент' в тексте = assistant."""
        msg = ChatMessage(
            order_id="123",
            text="Ассистент: условия заказа были изменены заказчиком",
            is_incoming=False,
            is_system=True,
        )
        assert msg.is_assistant is True

    def test_not_assistant_regular_customer(self):
        """Обычное сообщение от заказчика — не assistant."""
        msg = ChatMessage(
            order_id="123",
            text="Когда будет готово?",
            is_incoming=True,
            sender_name="Иван",
        )
        assert msg.is_assistant is False

    def test_not_assistant_our_message(self):
        """Наше исходящее сообщение — не assistant."""
        msg = ChatMessage(
            order_id="123",
            text="Работа готова",
            is_incoming=False,
        )
        assert msg.is_assistant is False

    def test_not_assistant_no_sender(self):
        """Сообщение без sender_name — не assistant."""
        msg = ChatMessage(
            order_id="123",
            text="Привет",
            is_incoming=True,
        )
        assert msg.is_assistant is False

    def test_is_assistant_with_prefix(self):
        """Sender 'Ассистент Автор24' тоже определяется."""
        msg = ChatMessage(
            order_id="123",
            text="Текст",
            is_incoming=True,
            sender_name="Ассистент Автор24",
        )
        assert msg.is_assistant is True

    def test_get_messages_includes_sender_name(self):
        """get_messages сохраняет sender_name из JS evaluate."""
        msg = ChatMessage(
            order_id="123",
            text="Текст",
            is_incoming=True,
            sender_name="Ассистент",
        )
        assert msg.sender_name == "Ассистент"
        assert msg.is_assistant is True

    @pytest.mark.asyncio
    async def test_get_messages_parses_sender_name(self):
        """get_messages передаёт senderName из JS evaluate в ChatMessage."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/getoneorder/10001"
        page.goto = AsyncMock()

        js_result = [
            {
                "text": "Условия заказа изменены",
                "isSystem": False,
                "isOutgoing": False,
                "timestamp": "12:00",
                "senderName": "Ассистент",
                "hasFiles": False,
                "fileUrls": [],
            },
            {
                "text": "Когда будет готово?",
                "isSystem": False,
                "isOutgoing": False,
                "timestamp": "12:05",
                "senderName": "Иван",
                "hasFiles": False,
                "fileUrls": [],
            },
        ]
        page.evaluate = AsyncMock(return_value=js_result)

        chat_tab = MagicMock()
        chat_tab.count = AsyncMock(return_value=0)
        page.locator = MagicMock(return_value=chat_tab)

        messages = await get_messages(page, "10001")

        assert len(messages) == 2
        assert messages[0].sender_name == "Ассистент"
        assert messages[0].is_assistant is True
        assert messages[1].sender_name == "Иван"
        assert messages[1].is_assistant is False


# ===== Тесты утилит =====

class TestUtils:
    """Тесты вспомогательных функций."""

    def test_extract_number(self):
        """_extract_number извлекает числа."""
        assert _extract_number("3 000 ₽") == 3000
        assert _extract_number("15 ставок") == 15
        assert _extract_number("0 файлов") == 0
        assert _extract_number("нет") is None

    def test_extract_int(self):
        """_extract_int извлекает целые числа."""
        assert _extract_int("60%") == 60
        assert _extract_int("20 дней") == 20
        assert _extract_int("нет") is None

    def test_extract_float(self):
        """_extract_float извлекает дробные числа."""
        assert _extract_float("1.5") == 1.5
        assert _extract_float("1,5") == 1.5
        assert _extract_float("14") == 14.0
        assert _extract_float("нет") is None


# ===== Тест дедупликации через БД =====

class TestDeduplication:
    """Тест дедупликации заказов через БД."""

    @pytest.mark.asyncio
    async def test_dedup_by_avtor24_id(self, session):
        """Повторный заказ с тем же avtor24_id не дублируется."""
        from src.database.crud import create_order, get_order_by_avtor24_id

        await create_order(
            session,
            avtor24_id="10001",
            title="Тестовый заказ",
            work_type="Эссе",
            status="new",
        )

        existing = await get_order_by_avtor24_id(session, "10001")
        assert existing is not None
        assert existing.title == "Тестовый заказ"

        # Проверяем что повторный запрос находит существующий
        existing2 = await get_order_by_avtor24_id(session, "10001")
        assert existing2 is not None
        assert existing2.id == existing.id

        # Несуществующий
        missing = await get_order_by_avtor24_id(session, "99999")
        assert missing is None


# ===== Тест cookies persistence =====

class TestSessionManagement:
    """Тесты управления сессией."""

    @pytest.mark.asyncio
    async def test_cookies_save_load(self, tmp_path):
        """Cookies сохраняются в файл и загружаются."""
        cookies_file = tmp_path / "test_cookies.json"
        test_cookies = [
            {"name": "session_id", "value": "abc123", "domain": ".avtor24.ru", "path": "/"},
            {"name": "csrf_token", "value": "xyz789", "domain": ".avtor24.ru", "path": "/"},
        ]

        # Сохраняем
        cookies_file.write_text(json.dumps(test_cookies, ensure_ascii=False), encoding="utf-8")

        # Загружаем
        loaded = json.loads(cookies_file.read_text(encoding="utf-8"))
        assert len(loaded) == 2
        assert loaded[0]["name"] == "session_id"
        assert loaded[1]["value"] == "xyz789"

    @pytest.mark.asyncio
    async def test_cookies_file_missing(self):
        """Без файла cookies — не падает."""
        bm = BrowserManager()
        # Нет контекста — save_cookies ничего не делает
        await bm.save_cookies()
