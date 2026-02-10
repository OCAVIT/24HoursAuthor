"""Парсинг детальной страницы заказа на Автор24 (React SPA)."""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Категории типов работ — определяют какие поля доступны на сайте
# ---------------------------------------------------------------------------

WORK_TYPE_CATEGORIES: dict[str, dict] = {
    "writing": {
        "types": [
            "Эссе", "Сочинение", "Реферат", "Доклад", "Курсовая работа",
            "Дипломная работа", "Выпускная квалификационная работа (ВКР)",
            "Статья", "Автореферат", "Аннотация",
            "Научно-исследовательская работа (НИР)", "Индивидуальный проект",
            "Маркетинговое исследование", "Бизнес-план", "Отчёт по практике",
            "Рецензия", "Вычитка и рецензирование работ", "Творческая работа",
            "Статья ВАК/Scopus", "Лабораторная работа", "Презентации",
            "Монография",
        ],
        "fields": ["pages", "font_size", "line_spacing", "uniqueness", "antiplagiat", "budget"],
    },
    "copywriting": {
        "types": [
            "Копирайтинг", "Набор текста", "Повышение уникальности текста",
            "Гуманизация работы",
        ],
        "fields": ["char_count", "uniqueness", "budget"],
    },
    "tasks": {
        "types": [
            "Решение задач", "Контрольная работа", "Ответы на вопросы",
            "Задача по программированию",
        ],
        "fields": ["budget"],
    },
    "other": {
        "types": [],  # fallback
        "fields": ["budget"],
    },
}

# Быстрый lookup: work_type → category name
_WORK_TYPE_TO_CATEGORY: dict[str, str] = {}
for _cat_name, _cat_data in WORK_TYPE_CATEGORIES.items():
    for _wt in _cat_data["types"]:
        _WORK_TYPE_TO_CATEGORY[_wt] = _cat_name


def get_work_type_category(work_type: str) -> str:
    """Определить категорию типа работы."""
    return _WORK_TYPE_TO_CATEGORY.get(work_type, "other")


def get_category_fields(work_type: str) -> list[str]:
    """Получить список полей, доступных для данного типа работы."""
    cat = get_work_type_category(work_type)
    return WORK_TYPE_CATEGORIES[cat]["fields"]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class OrderDetail:
    """Полная информация о заказе."""
    order_id: str
    title: str
    url: str
    work_type: str = ""
    subject: str = ""
    description: str = ""
    pages_min: Optional[int] = None
    pages_max: Optional[int] = None
    font_size: int = 14
    line_spacing: float = 1.5
    required_uniqueness: Optional[int] = None
    antiplagiat_system: str = ""
    deadline: Optional[str] = None
    budget: Optional[str] = None
    budget_rub: Optional[int] = None
    average_bid: Optional[int] = None
    customer_name: str = ""
    customer_online: str = ""
    customer_badges: list[str] = field(default_factory=list)
    creation_time: str = ""
    file_names: list[str] = field(default_factory=list)
    file_urls: list[str] = field(default_factory=list)
    formatting_requirements: str = ""
    structure: str = ""
    special_requirements: str = ""
    extracted_from_files: bool = False


def _extract_int(text: str) -> Optional[int]:
    """Извлечь целое число из строки."""
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else None


def _extract_float(text: str) -> Optional[float]:
    """Извлечь дробное число из строки."""
    match = re.search(r"(\d+[.,]?\d*)", text.replace(" ", ""))
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def _parse_pages(text: str) -> tuple[Optional[int], Optional[int]]:
    """Извлечь мин/макс страниц из строки вида 'от 10 до 20' или '20 стр'."""
    # "от X до Y"
    range_match = re.search(r"от\s*(\d+)\s*до\s*(\d+)", text)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))
    # Просто число
    num = _extract_int(text)
    if num:
        return num, num
    return None, None


async def fetch_order_detail(page: Page, order_url: str) -> OrderDetail:
    """Парсинг полной страницы заказа (React SPA).

    URL формат: /order/getoneorder/{id}
    Страница рендерится через React в div#root.
    """
    full_url = order_url
    if not order_url.startswith("http"):
        full_url = settings.avtor24_base_url + order_url

    await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
    await browser_manager.short_delay()

    # ID из URL
    match = re.search(r"/order/getoneorder/(\d+)", full_url)
    if not match:
        match = re.search(r"/order/(\d+)", full_url)
    order_id = match.group(1) if match else ""

    # Ожидаем загрузку React-компонентов
    try:
        await page.wait_for_selector(
            '[class*="AuctionDetailsStyled"], [class*="OrderStyled"]',
            timeout=15000,
        )
        # Дополнительная задержка для полного рендера
        await browser_manager.short_delay()
    except Exception:
        logger.warning("Детали заказа не загрузились за 15 сек")

    # Извлекаем данные через JS (быстрее, чем множественные Playwright-запросы)
    raw = await page.evaluate("""
        () => {
            let root = document.querySelector('#root');
            if (!root) return {error: 'no root'};

            // Заголовок
            let titleEl = root.querySelector('[class*="styled__Title"]');
            let title = titleEl ? titleEl.textContent.trim() : '';

            // Информационные поля — каждый FieldStyled содержит 2 child: label + value
            let fields = {};
            root.querySelectorAll('[class*="FieldStyled"]').forEach(field => {
                let children = field.children;
                if (children.length >= 2) {
                    let label = children[0].textContent.trim();
                    let value = children[1].textContent.trim();
                    fields[label] = value;
                }
            });

            // Бюджет (BudgetFieldStyled — отдельный блок)
            let budgetEl = root.querySelector('[class*="BudgetFieldStyled"]');
            let budgetText = '';
            if (budgetEl && budgetEl.children.length >= 2) {
                budgetText = budgetEl.children[1].textContent.trim();
            }

            // Описание (DescriptionStyled — 2 child: заголовок + текст)
            let descEl = root.querySelector('[class*="DescriptionStyled"]');
            let description = '';
            if (descEl) {
                // Берём текст всех children кроме первого (заголовка "Описание заказа")
                let children = Array.from(descEl.children);
                if (children.length > 1) {
                    description = children.slice(1).map(c => c.textContent.trim()).join('\\n');
                } else {
                    description = descEl.textContent.trim();
                    description = description.replace(/^Описание заказа\\s*/, '');
                }
            }

            // Заказчик
            let customerEl = root.querySelector('[class*="CustomerStyled"]');
            let customerName = '';
            let customerOnline = '';
            if (customerEl) {
                let allText = customerEl.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
                // Пропускаем метку "Заказчик" и строки со статусом онлайн
                customerName = allText.find(t =>
                    t !== 'Заказчик' && !t.includes('онлайн') && !t.includes('назад') && !t.includes('сейчас на сайте')
                ) || '';
                // Онлайн-статус
                let labelEl = customerEl.querySelector('[class*="Label"]');
                customerOnline = labelEl ? labelEl.textContent.trim() : '';
            }

            // Средняя ставка
            let avgBidEl = root.querySelector('[class*="AvgBid"]');
            let avgBid = avgBidEl ? avgBidEl.textContent.trim() : '';

            // Файлы: имена + URL-ы для скачивания
            let fileNames = [];
            let fileUrls = [];
            root.querySelectorAll('[class*="ItemStyled"]').forEach(item => {
                // ItemStyled содержит: номер, иконку расширения, имя файла, размер
                let texts = item.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
                // Ищем имя файла (обычно 3-й элемент, содержит расширение)
                for (let t of texts) {
                    if (/\\.(docx?|pdf|xlsx?|pptx?|txt|zip|rar|jpg|jpeg|png|heic|csv)$/i.test(t)) {
                        fileNames.push(t);
                        break;
                    }
                }
                // Ищем ссылку на скачивание
                let link = item.querySelector('a[href]');
                if (link) {
                    let href = link.getAttribute('href');
                    if (href) fileUrls.push(href);
                }
            });

            // Время создания
            let timeEl = root.querySelector('[class*="OrderCreationStyled"]');
            let creationTime = timeEl ? timeEl.textContent.trim() : '';

            // Бейджи (Постоянный клиент, и т.д.)
            let badges = [];
            root.querySelectorAll('[class*="BadgeContent"]').forEach(el => {
                let text = el.textContent.trim();
                if (text) badges.push(text);
            });

            return {
                title,
                fields,
                budgetText,
                description,
                customerName,
                customerOnline,
                avgBid,
                fileNames,
                fileUrls,
                creationTime,
                badges,
            };
        }
    """)

    if raw.get("error"):
        logger.error("Ошибка парсинга детали заказа: %s", raw["error"])
        return OrderDetail(order_id=order_id, title="", url=full_url)

    # Маппинг полей из JS в OrderDetail
    fields = raw.get("fields", {})

    work_type = fields.get("Тип работы", "")
    subject = fields.get("Предмет", "")
    deadline = fields.get("Срок сдачи", None)

    # Определяем категорию для graceful field handling
    available_fields = get_category_fields(work_type) if work_type else ["pages", "font_size", "line_spacing", "uniqueness", "antiplagiat", "budget"]

    # Страницы — только если категория предусматривает
    pages_min, pages_max = None, None
    if "pages" in available_fields:
        pages_text = fields.get("Кол-во страниц", "") or fields.get("Минимальный объём", "")
        if pages_text:
            pages_min, pages_max = _parse_pages(pages_text)
        if pages_min is None:
            min_vol = fields.get("Минимальный объём", "")
            if min_vol:
                num = _extract_int(min_vol)
                if num:
                    pages_min = num

    # Шрифт
    font_size = 14
    if "font_size" in available_fields:
        font_text = fields.get("Шрифт", "")
        if font_text:
            size = _extract_int(font_text)
            if size:
                font_size = size

    # Интервал
    line_spacing = 1.5
    if "line_spacing" in available_fields:
        spacing_text = fields.get("Интервал", "")
        if spacing_text:
            sp = _extract_float(spacing_text)
            if sp:
                line_spacing = sp

    # Оригинальность
    required_uniqueness = None
    if "uniqueness" in available_fields:
        uniq_text = fields.get("Оригинальность", "")
        if uniq_text:
            required_uniqueness = _extract_int(uniq_text)

    # Антиплагиат
    antiplagiat_system = ""
    if "antiplagiat" in available_fields:
        antiplagiat_system = fields.get("Антиплагиат", "")

    # Бюджет — всегда парсим
    budget_text = raw.get("budgetText", "")
    budget_rub = _extract_int(budget_text) if budget_text else None

    # Средняя ставка
    avg_bid_text = raw.get("avgBid", "")
    average_bid = _extract_int(avg_bid_text) if avg_bid_text else None

    return OrderDetail(
        order_id=order_id,
        title=raw.get("title", ""),
        url=full_url,
        work_type=work_type,
        subject=subject,
        description=raw.get("description", ""),
        pages_min=pages_min,
        pages_max=pages_max,
        font_size=font_size,
        line_spacing=line_spacing,
        required_uniqueness=required_uniqueness,
        antiplagiat_system=antiplagiat_system,
        deadline=deadline,
        budget=budget_text or None,
        budget_rub=budget_rub,
        average_bid=average_bid,
        customer_name=raw.get("customerName", ""),
        customer_online=raw.get("customerOnline", ""),
        customer_badges=raw.get("badges", []),
        creation_time=raw.get("creationTime", ""),
        file_names=raw.get("fileNames", []),
        file_urls=raw.get("fileUrls", []),
    )
