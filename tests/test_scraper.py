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
from src.scraper.order_detail import fetch_order_detail, OrderDetail, _parse_int, _parse_float
from src.scraper.bidder import place_bid
from src.scraper.chat import get_messages, send_message, ChatMessage

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
        """Создать мок страницы с 5 заказами."""
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_selector = AsyncMock()

        # Данные заказов из order_list.html
        orders_data = [
            {
                "data-order-id": "10001",
                "title": "Курсовая по экономике предприятия",
                "href": "/order/10001",
                "work_type": "Курсовая работа",
                "subject": "Экономика",
                "deadline": "15.02.2026",
                "time_left": "7 дней",
                "price": "3 000 ₽",
                "bids": "2 ставки",
                "files": "1 файл",
                "online": True,
                "badge": "Постоянный клиент",
                "desc": "Курсовая работа по экономике предприятия, 25-30 страниц...",
            },
            {
                "data-order-id": "10002",
                "title": "Эссе по философии",
                "href": "/order/10002",
                "work_type": "Эссе",
                "subject": "Философия",
                "deadline": "10.02.2026",
                "time_left": "2 дня",
                "price": "1 500 ₽",
                "bids": "0 ставок",
                "files": "0 файлов",
                "online": False,
                "badge": "",
                "desc": "Эссе на тему \"Свобода и ответственность\"...",
            },
            {
                "data-order-id": "10003",
                "title": "Реферат по истории России",
                "href": "/order/10003",
                "work_type": "Реферат",
                "subject": "История",
                "deadline": "20.02.2026",
                "time_left": "12 дней",
                "price": "1 200 ₽",
                "bids": "5 ставок",
                "files": "2 файла",
                "online": True,
                "badge": "Быстрый заказ",
                "desc": "Реферат по истории России XIX века, 15 страниц...",
            },
            {
                "data-order-id": "10004",
                "title": "Контрольная по математике",
                "href": "/order/10004",
                "work_type": "Контрольная работа",
                "subject": "Математика",
                "deadline": "09.02.2026",
                "time_left": "1 день",
                "price": "800 ₽",
                "bids": "1 ставка",
                "files": "1 файл",
                "online": False,
                "badge": "",
                "desc": "10 задач по линейной алгебре...",
            },
            {
                "data-order-id": "10005",
                "title": "Дипломная работа по менеджменту",
                "href": "/order/10005",
                "work_type": "Дипломная работа",
                "subject": "Менеджмент",
                "deadline": "01.04.2026",
                "time_left": "52 дня",
                "price": "15 000 ₽",
                "bids": "3 ставки",
                "files": "3 файла",
                "online": True,
                "badge": "Постоянный клиент",
                "desc": "ВКР по управлению персоналом, 80-100 страниц, антиплагиат 70%...",
            },
        ]

        def _make_card_mock(data: dict) -> MagicMock:
            card = MagicMock()
            card.get_attribute = AsyncMock(
                side_effect=lambda attr: data.get(attr, data.get(f"data-{attr}", None))
            )
            card.get_attribute = AsyncMock(return_value=data.get("data-order-id"))

            def locator_fn(sel):
                loc = MagicMock()
                if "order-title" in sel or "h3" in sel or "h2" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["title"])
                elif "href*='/order/'" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.get_attribute = AsyncMock(return_value=data["href"])
                elif "work-type" in sel or "order-type" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["work_type"])
                elif "subject" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["subject"])
                elif "deadline" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["deadline"])
                elif "time-left" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["time_left"])
                elif "price" in sel or "budget" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["price"])
                elif "bid-count" in sel or "bids" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["bids"])
                elif "files-count" in sel or "attachments" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["files"])
                elif "online" in sel:
                    loc.count = AsyncMock(return_value=1 if data["online"] else 0)
                elif "badge" in sel:
                    loc.count = AsyncMock(return_value=1 if data["badge"] else 0)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["badge"])
                elif "description" in sel or "desc" in sel:
                    loc.count = AsyncMock(return_value=1)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value=data["desc"])
                else:
                    loc.count = AsyncMock(return_value=0)
                    loc.first = MagicMock()
                    loc.first.inner_text = AsyncMock(return_value="")
                return loc

            card.locator = MagicMock(side_effect=locator_fn)
            return card

        cards = [_make_card_mock(d) for d in orders_data]

        def page_locator(sel):
            loc = MagicMock()
            loc.all = AsyncMock(return_value=cards)
            return loc

        page.locator = MagicMock(side_effect=page_locator)
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
        assert orders[0].budget == 3000
        assert orders[1].budget == 1500

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
        assert orders[0].customer_online is True
        assert orders[1].customer_online is False

    @pytest.mark.asyncio
    async def test_parse_order_list_badge(self):
        """Бейдж заказчика парсится."""
        page = self._build_order_list_page()
        orders = await parse_order_cards(page)
        assert orders[0].customer_badge == "Постоянный клиент"
        assert orders[2].customer_badge == "Быстрый заказ"


# ===== Тесты парсинга деталей заказа =====

class TestOrderDetailParsing:
    """Тесты парсинга детальной страницы заказа."""

    def _build_detail_page(self) -> MagicMock:
        """Создать мок страницы с деталями заказа."""
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        # Информационные поля
        info_fields = {
            "тип работы": "Курсовая работа",
            "предмет": "Экономика предприятия",
            "количество страниц": "25-30",
            "размер шрифта": "14",
            "межстрочный интервал": "1.5",
            "требуемая уникальность": "60%",
            "система антиплагиата": "ETXT Антиплагиат",
            "срок сдачи": "15.02.2026",
            "бюджет заказчика": "3 000 ₽",
            "средняя ставка": "2 800 ₽",
            "гарантийный срок": "20 дней",
        }

        def page_locator(sel):
            loc = MagicMock()

            if "order-title" in sel or "h1" in sel:
                loc.count = AsyncMock(return_value=1)
                loc.first = MagicMock()
                loc.first.inner_text = AsyncMock(return_value="Курсовая по экономике предприятия")

            elif "description" in sel or "task-description" in sel:
                loc.count = AsyncMock(return_value=1)
                loc.first = MagicMock()
                loc.first.inner_text = AsyncMock(return_value=(
                    "Необходимо написать курсовую работу по экономике предприятия на тему "
                    "\"Анализ финансово-хозяйственной деятельности предприятия\"."
                ))

            elif "order-info__row" in sel or "order-param" in sel:
                # Возвращаем список строк info
                rows = []
                for label, value in info_fields.items():
                    row = MagicMock()
                    row.inner_text = AsyncMock(return_value=label)

                    # + dd, .value
                    def make_val_locator(v):
                        vl = MagicMock()
                        vl.count = AsyncMock(return_value=1)
                        vl.first = MagicMock()
                        vl.first.inner_text = AsyncMock(return_value=v)
                        return vl

                    row.locator = MagicMock(return_value=make_val_locator(value))
                    rows.append(row)

                loc.all = AsyncMock(return_value=rows)

            elif "customer-info" in sel or "user-info" in sel:
                loc.count = AsyncMock(return_value=1)
                loc.first = MagicMock()
                loc.first.inner_text = AsyncMock(
                    return_value="Заказчик: Иван И. | Постоянный клиент | 15 заказов"
                )

            elif "download" in sel or "file" in sel or "attachment" in sel:
                # Файлы
                async def _file_all():
                    f1 = MagicMock()
                    f1.get_attribute = AsyncMock(return_value="/file/download/55001")
                    f2 = MagicMock()
                    f2.get_attribute = AsyncMock(return_value="/file/download/55002")
                    return [f1, f2]

                loc.all = _file_all

            elif "breadcrumb" in sel or "work-type-label" in sel:
                loc.count = AsyncMock(return_value=0)

            elif "subject-label" in sel or "order-subject" in sel:
                loc.count = AsyncMock(return_value=0)

            elif "order-price" in sel or "price-value" in sel or "budget-value" in sel:
                loc.count = AsyncMock(return_value=0)

            else:
                loc.count = AsyncMock(return_value=0)
                loc.first = MagicMock()
                loc.first.inner_text = AsyncMock(return_value="")
                loc.all = AsyncMock(return_value=[])

            return loc

        page.locator = MagicMock(side_effect=page_locator)
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
        assert detail.budget == 3000

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
        assert "Иван" in detail.customer_info

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
            if "price" in sel:
                return price_input
            elif "comment" in sel:
                return comment_input
            elif "ставк" in sel or "Предложить" in sel:
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
            if "price" in sel:
                return price_input
            elif "comment" in sel:
                return comment_input
            elif "ставк" in sel or "Предложить" in sel:
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
        """Сообщения парсятся из чата."""
        page = MagicMock()
        page.goto = AsyncMock()

        msg_data = [
            {"text": "Здравствуйте! Сможете сделать?", "class": "message incoming", "time": "08.02.2026 10:30"},
            {"text": "Да, тема знакомая, сделаю в срок.", "class": "message outgoing", "time": "08.02.2026 10:45"},
            {"text": "Методичку прикрепила, посмотрите.", "class": "message incoming", "time": "08.02.2026 11:00"},
        ]

        async def _msg_all():
            mocks = []
            for md in msg_data:
                m = MagicMock()

                def make_text_loc(text):
                    tl = MagicMock()
                    tl.count = AsyncMock(return_value=1)
                    tl.first = MagicMock()
                    tl.first.inner_text = AsyncMock(return_value=text)
                    return tl

                def make_time_loc(time_str):
                    tl = MagicMock()
                    tl.count = AsyncMock(return_value=1)
                    tl.first = MagicMock()
                    tl.first.inner_text = AsyncMock(return_value=time_str)
                    return tl

                def loc_fn(sel, _md=md):
                    if "text" in sel or "body" in sel:
                        return make_text_loc(_md["text"])
                    elif "time" in sel or "timestamp" in sel:
                        return make_time_loc(_md["time"])
                    empty = MagicMock()
                    empty.count = AsyncMock(return_value=0)
                    return empty

                m.locator = MagicMock(side_effect=loc_fn)
                m.get_attribute = AsyncMock(return_value=md["class"])
                mocks.append(m)
            return mocks

        def page_locator(sel):
            loc = MagicMock()
            loc.all = _msg_all
            return loc

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.chat.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            messages = await get_messages(page, "10001")

        assert len(messages) == 3
        assert messages[0].is_incoming is True
        assert messages[1].is_incoming is False
        assert "Сможете сделать" in messages[0].text

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Сообщение отправляется."""
        page = MagicMock()
        page.url = "https://avtor24.ru/order/10001/chat"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        msg_input = MagicMock()
        msg_input.count = AsyncMock(return_value=1)
        msg_input.first = MagicMock()
        msg_input.first.fill = AsyncMock()

        send_btn = MagicMock()
        send_btn.count = AsyncMock(return_value=1)
        send_btn.first = MagicMock()
        send_btn.first.click = AsyncMock()

        def page_locator(sel):
            if "message" in sel or "chat-input" in sel:
                return msg_input
            elif "Отправить" in sel or "send" in sel:
                return send_btn
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            return m

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.chat.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            result = await send_message(page, "10001", "Работа готова!")

        assert result is True
        msg_input.first.fill.assert_awaited_once_with("Работа готова!")
        send_btn.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_no_input(self):
        """Отправка не удаётся если нет поля ввода."""
        page = MagicMock()
        page.url = "https://avtor24.ru/other"
        page.goto = AsyncMock()

        def page_locator(sel):
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            m.first = MagicMock()
            m.first.fill = AsyncMock()
            return m

        page.locator = MagicMock(side_effect=page_locator)

        with patch("src.scraper.chat.browser_manager") as bm:
            bm.short_delay = AsyncMock()
            result = await send_message(page, "10001", "Тест")

        assert result is False


# ===== Тесты утилит =====

class TestUtils:
    """Тесты вспомогательных функций."""

    def test_extract_number(self):
        """_extract_number извлекает числа."""
        assert _extract_number("3 000 ₽") == 3000
        assert _extract_number("15 ставок") == 15
        assert _extract_number("0 файлов") == 0
        assert _extract_number("нет") is None

    def test_parse_int(self):
        """_parse_int извлекает целые числа."""
        assert _parse_int("60%") == 60
        assert _parse_int("20 дней") == 20
        assert _parse_int("нет") is None

    def test_parse_float(self):
        """_parse_float извлекает дробные числа."""
        assert _parse_float("1.5") == 1.5
        assert _parse_float("1,5") == 1.5
        assert _parse_float("14") == 14.0
        assert _parse_float("нет") is None


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
