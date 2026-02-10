"""Чтение и отправка сообщений в чат заказчика на Автор24.

Чат находится на странице заказа: /order/getoneorder/{order_id}
Это React SPA со styled-components.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Page

from src.config import settings
from src.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Сообщение из чата."""
    order_id: str
    text: str
    is_incoming: bool  # True = от заказчика, False = от нас
    timestamp: Optional[str] = None
    is_system: bool = False  # "Вы сделали ставку", "Вас выбрали автором" и т.д.
    has_files: bool = False  # Есть ли прикреплённые файлы
    file_urls: list = None  # URL файлов для скачивания
    sender_name: Optional[str] = None  # Имя отправителя ("Ассистент", имя заказчика, etc.)

    def __post_init__(self):
        if self.file_urls is None:
            self.file_urls = []

    @property
    def is_assistant(self) -> bool:
        """Сообщение от платформенного Ассистента (изменение условий заказа)."""
        if self.sender_name and "ассистент" in self.sender_name.lower():
            return True
        if self.is_system and self.text and "ассистент" in self.text.lower():
            return True
        return False


def _order_page_url(order_id: str) -> str:
    """URL страницы заказа (где живёт чат)."""
    return f"{settings.avtor24_base_url}/order/getoneorder/{order_id}"


async def _ensure_order_page(page: Page, order_id: str) -> None:
    """Убедиться что мы на странице нужного заказа."""
    current = page.url
    if f"/order/getoneorder/{order_id}" not in current:
        await page.goto(_order_page_url(order_id),
                        wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)


async def _ensure_chat_tab(page: Page) -> None:
    """Кликнуть на вкладку 'Чат с заказчиком' если не активна."""
    try:
        chat_tab = page.locator('button:has-text("Чат с заказчиком")')
        if await chat_tab.count() > 0:
            await chat_tab.first.click()
            await asyncio.sleep(1)
    except Exception:
        pass


async def get_order_page_info(page: Page, order_id: str) -> dict:
    """Извлечь информацию со страницы заказа (статус, чат, кнопки).

    Returns dict с ключами:
      - accepted: bool (Вас выбрали автором)
      - has_confirm_btn: bool (кнопка Подтвердить)
      - has_bid_form: bool
      - has_chat: bool
      - messages: list[dict]
      - page_text: str
    """
    await _ensure_order_page(page, order_id)
    await _ensure_chat_tab(page)
    await asyncio.sleep(2)

    return await page.evaluate("""
        () => {
            const root = document.querySelector('#root');
            if (!root) return {error: 'no root'};

            const fullText = root.innerText || '';

            // Статус
            const accepted = fullText.includes('Вас выбрали автором');
            const hasConfirmBtn = !!root.querySelector('button:has(div)');
            let confirmBtnFound = false;
            root.querySelectorAll('button').forEach(btn => {
                if ((btn.innerText || '').trim() === 'Подтвердить') confirmBtnFound = true;
            });
            const hasBidForm = !!root.querySelector('#MakeOffer__inputBid');
            const hasChat = !!root.querySelector('textarea');

            // Извлекаем сообщения из чата
            const messages = [];
            const groupItems = root.querySelectorAll('[class*="GroupItem"]');
            groupItems.forEach(item => {
                const text = (item.innerText || '').trim();
                if (!text) return;

                // Системные сообщения
                const isSystem = !!item.querySelector('[class*="MessageSystemStyled"]');

                // Определяем направление: исходящие = наши сообщения
                // На Avtor24 исходящие обычно справа (имеют другой styled)
                const msgBase = item.querySelector('[class*="MessageBaseStyled"]');
                let isOutgoing = false;
                if (msgBase) {
                    const cls = msgBase.className || '';
                    // Проверяем наличие класса, указывающего на исходящее
                    // Обычно исходящие имеют другой цвет/расположение
                    // Ищем по классу или по parent контейнеру
                    const parent = msgBase.closest('[class*="GroupStyled"]');
                    if (parent) {
                        // Если есть аватар с нашим именем или метка "Вы"
                        const parentText = parent.innerText || '';
                        // Исходящие обычно не имеют имени заказчика
                    }
                }

                // Время
                let timestamp = '';
                const timeEl = item.querySelector('[class*="Time"], time, [class*="timestamp"]');
                if (timeEl) timestamp = (timeEl.innerText || '').trim();

                messages.push({
                    text: text.substring(0, 2000),
                    isSystem,
                    isOutgoing,
                    timestamp,
                });
            });

            return {
                accepted,
                hasConfirmBtn: confirmBtnFound,
                hasBidForm,
                hasChat,
                messages,
                pageText: fullText.substring(0, 3000),
            };
        }
    """)


async def get_messages(page: Page, order_id: str) -> list[ChatMessage]:
    """Получить историю сообщений чата заказа."""
    try:
        await _ensure_order_page(page, order_id)
        await _ensure_chat_tab(page)
        await asyncio.sleep(2)

        raw = await page.evaluate("""
            () => {
                const root = document.querySelector('#root');
                if (!root) return [];

                const messages = [];
                const items = root.querySelectorAll('[class*="GroupItem"]');

                items.forEach(item => {
                    const text = (item.innerText || '').trim();
                    if (!text) return;

                    const isSystem = !!item.querySelector('[class*="MessageSystemStyled"]');

                    // Извлекаем имя отправителя из заголовка группы
                    let senderName = '';
                    const group = item.closest('[class*="GroupStyled"], [class*="Group"]');
                    if (group) {
                        const nameEl = group.querySelector(
                            '[class*="NameStyled"], [class*="Name"], [class*="AuthorName"], [class*="Sender"]'
                        );
                        if (nameEl) {
                            senderName = (nameEl.textContent || '').trim();
                        }
                    }

                    // Определяем направление
                    // На Avtor24: у исходящих сообщений MessageBaseStyled
                    // имеет другой стиль/цвет (обычно синий/серый)
                    const msgBase = item.querySelector('[class*="MessageBaseStyled"]');
                    let isOutgoing = false;

                    if (msgBase && !isSystem) {
                        // Проверяем наличие класса для исходящих
                        const cls = msgBase.className || '';
                        // Ищем специфичные стили для outgoing
                        // Обычно содержат "Out" или имеют другой вариант styled
                        if (cls.includes('Out') || cls.includes('My') || cls.includes('Author')) {
                            isOutgoing = true;
                        }

                        // Альтернатива: проверяем computed style (цвет фона)
                        const style = window.getComputedStyle(msgBase);
                        const bg = style.backgroundColor;
                        // Исходящие обычно имеют синий/голубой фон
                        if (bg && (bg.includes('66') || bg.includes('33') || bg.includes('99'))) {
                            isOutgoing = true;
                        }
                    }

                    let timestamp = '';
                    const timeEl = item.querySelector('[class*="Time"]');
                    if (timeEl) timestamp = (timeEl.innerText || '').trim();

                    // Обнаружение прикреплённых файлов
                    const fileUrls = [];
                    // Ищем ссылки на файлы (download links, file attachments)
                    const fileLinks = item.querySelectorAll(
                        'a[href*="/download/"], a[href*="/file/"], a[href*="/attachment/"], ' +
                        'a[href*="/ajax/"], a[download], ' +
                        '[class*="FileStyled"] a, [class*="Attachment"] a, [class*="file"] a'
                    );
                    fileLinks.forEach(link => {
                        const href = link.href || link.getAttribute('href');
                        if (href) fileUrls.push(href);
                    });
                    // Также ищем элементы с классом, похожим на файл
                    const fileElements = item.querySelectorAll(
                        '[class*="FileStyled"], [class*="AttachmentStyled"], [class*="FileMessage"]'
                    );
                    fileElements.forEach(el => {
                        const link = el.querySelector('a');
                        if (link) {
                            const href = link.href || link.getAttribute('href');
                            if (href && !fileUrls.includes(href)) fileUrls.push(href);
                        }
                    });

                    messages.push({
                        text: text.substring(0, 2000),
                        isSystem,
                        isOutgoing,
                        timestamp,
                        hasFiles: fileUrls.length > 0,
                        fileUrls: fileUrls,
                        senderName: senderName,
                    });
                });

                return messages;
            }
        """)

        result = []
        for msg in raw:
            file_urls = msg.get("fileUrls", [])
            has_files = msg.get("hasFiles", False) or len(file_urls) > 0
            sender_name = msg.get("senderName", "") or None
            if msg.get("isSystem"):
                result.append(ChatMessage(
                    order_id=order_id,
                    text=msg["text"],
                    is_incoming=False,
                    timestamp=msg.get("timestamp"),
                    is_system=True,
                    has_files=has_files,
                    file_urls=file_urls,
                    sender_name=sender_name,
                ))
            else:
                result.append(ChatMessage(
                    order_id=order_id,
                    text=msg["text"],
                    is_incoming=not msg.get("isOutgoing", False),
                    timestamp=msg.get("timestamp"),
                    has_files=has_files,
                    file_urls=file_urls,
                    sender_name=sender_name,
                ))

        return result

    except Exception as e:
        logger.error("Ошибка получения сообщений для заказа %s: %s", order_id, e)
        return []


async def _navigate_home(page: Page) -> bool:
    """Перейти на /home и дождаться загрузки.

    Returns True если страница загрузилась, False при ошибке/redirect на login.
    """
    home_url = f"{settings.avtor24_base_url}/home"
    try:
        await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as nav_err:
        if "ERR_ABORTED" in str(nav_err):
            logger.debug("ERR_ABORTED на /home, ждём загрузки страницы...")
            await asyncio.sleep(5)
            if "/login" in page.url:
                logger.warning("Сессия истекла, требуется повторный логин")
                return False
        else:
            raise
    await asyncio.sleep(5)
    return True


async def _click_home_tab(page: Page, tab_text: str) -> bool:
    """Кликнуть на вкладку на /home (Активные чаты / В работе / Ждут подтверждения / В аукционе).

    Вкладки реализованы как <span class="header-filter-item"> внутри <li>.
    Клик по родительскому <li> переключает вид.
    """
    clicked = await page.evaluate("""
        (tabText) => {
            const spans = document.querySelectorAll('.header-filter-item, span');
            for (const span of spans) {
                const text = (span.textContent || '').trim();
                if (text === tabText || text.includes(tabText)) {
                    const target = span.closest('li') || span;
                    target.click();
                    return true;
                }
            }
            return false;
        }
    """, tab_text)
    if clicked:
        await asyncio.sleep(3)  # ждём обновления React-контента
    else:
        logger.warning("Вкладка '%s' не найдена на /home", tab_text)
    return clicked


async def _extract_visible_order_ids(page: Page) -> list[str]:
    """Извлечь order_id из текущего вида на /home (document.body).

    ВАЖНО: Контент рендерится ВНЕ #root — ищем по document.body.
    Исключаем рекомендованные заказы (with ?from_recommended= в URL).
    """
    raw: list[str] = await page.evaluate(r"""
        () => {
            const results = [];
            const seen = new Set();
            document.querySelectorAll('a[href*="/order/getoneorder/"]').forEach(a => {
                if (a.href.includes('from_recommended')) return;
                const match = a.href.match(/getoneorder\/(\d+)/);
                if (!match || seen.has(match[1])) return;
                seen.add(match[1]);
                results.push(match[1]);
            });
            return results;
        }
    """)
    return raw


async def get_accepted_order_ids(page: Page) -> list[str]:
    """Получить order_id из вкладки «Активные чаты» на /home.

    Это заказы, где заказчик уже выбрал нас автором.
    Используется для перевода bid_placed → accepted.

    Вкладка «Активные чаты» — дефолтная на /home, показывает
    все заказы где мы назначены автором (в работе / доставлены).
    """
    try:
        if not await _navigate_home(page):
            return []

        # «Активные чаты» — дефолтная вкладка, кликать не нужно
        active_ids = await _extract_visible_order_ids(page)

        if active_ids:
            logger.info(
                "Найдено %d заказов в «Активные чаты» на /home",
                len(active_ids),
            )
        return active_ids

    except Exception as e:
        logger.error("Ошибка получения активных заказов с /home: %s", e)
        return []


async def get_waiting_confirmation_order_ids(page: Page) -> list[str]:
    """Получить order_id заказов со статусом «Ждут подтверждения» на /home.

    Кликает вкладку «Ждут подтверждения» и извлекает order_id.
    Эти заказы требуют нажатия кнопки «Подтвердить» на странице заказа.
    """
    try:
        if not await _navigate_home(page):
            return []

        if not await _click_home_tab(page, "Ждут подтверждения"):
            logger.warning("Не удалось переключить на вкладку «Ждут подтверждения»")
            return []

        waiting_ids = await _extract_visible_order_ids(page)

        if waiting_ids:
            logger.info(
                "Найдено %d заказов «Ждут подтверждения» на /home",
                len(waiting_ids),
            )
        return waiting_ids

    except Exception as e:
        logger.error("Ошибка получения заказов «Ждут подтверждения»: %s", e)
        return []


async def get_active_chats(page: Page) -> list[str]:
    """Получить список order_id с активными чатами (в работе).

    Кликает вкладку «Активные чаты» (дефолтная) и извлекает order_id.
    Показывает заказы где мы назначены автором и работа активна.
    """
    try:
        if not await _navigate_home(page):
            return []

        # «Активные чаты» — дефолтная вкладка, но кликнем на всякий случай
        # (если до этого переключались на другую вкладку)
        await _click_home_tab(page, "Активные чаты")

        active_ids = await _extract_visible_order_ids(page)

        logger.info(
            "Найдено %d активных чатов на /home",
            len(active_ids),
        )
        return active_ids

    except Exception as e:
        logger.error("Ошибка получения списка чатов: %s", e)
        return []


async def send_message(page: Page, order_id: str, text: str) -> bool:
    """Отправить сообщение в чат заказа.

    Использует textarea на странице /order/getoneorder/{order_id}.
    Печатает текст с имитацией набора (type вместо fill) для естественности.
    """
    try:
        await _ensure_order_page(page, order_id)
        await _ensure_chat_tab(page)
        await asyncio.sleep(1)

        # Поле ввода: textarea с placeholder "Ваш ответ"
        msg_input = page.locator('textarea')
        if await msg_input.count() == 0:
            logger.error("Не найден textarea для заказа %s", order_id)
            return False

        await msg_input.first.click()
        await asyncio.sleep(0.5)

        # Имитация набора текста (type с задержкой между символами)
        await msg_input.first.fill("")  # очистить
        await msg_input.first.type(text, delay=30)  # ~30мс между символами
        await asyncio.sleep(1)

        # Отправка через JS (кнопка может быть скрыта Playwright'ом)
        sent = await page.evaluate("""
            () => {
                let btn = document.querySelector('[data-testid="dialogMessageInput-action_sendMsg"]');
                if (btn) { btn.click(); return true; }
                btn = document.querySelector('[class*="SendAction"]');
                if (btn) { btn.click(); return true; }
                return false;
            }
        """)

        if not sent:
            # Fallback: Ctrl+Enter
            await msg_input.first.press("Control+Enter")

        await asyncio.sleep(2)

        logger.info("Сообщение отправлено в чат заказа %s", order_id)
        return True

    except Exception as e:
        logger.error("Ошибка отправки сообщения в заказ %s: %s", order_id, e)
        return False


async def confirm_order(page: Page, order_id: str) -> bool:
    """Нажать 'Подтвердить' на странице заказа (подтвердить начало работы).

    После нажатия первой кнопки появляется модальное окно
    "Вы уверены, что хотите подтвердить начало работы..." —
    нужно нажать подтверждение и в модалке тоже.
    """
    try:
        await _ensure_order_page(page, order_id)
        await asyncio.sleep(2)

        # Ищем кнопку "Подтвердить" на странице
        confirm_btn = page.locator('button:has-text("Подтвердить")')
        if await confirm_btn.count() == 0:
            logger.warning("Кнопка 'Подтвердить' не найдена для заказа %s", order_id)
            return False

        await confirm_btn.first.click()
        await asyncio.sleep(2)

        # После клика появляется модальное окно подтверждения
        # Ищем кнопку подтверждения в модалке (data-testid="alertModal")
        modal_confirm = page.locator('[data-testid*="alertModal"] button:has-text("Подтвердить")')
        if await modal_confirm.count() > 0:
            await modal_confirm.first.click()
            logger.info("Нажата кнопка подтверждения в модалке для заказа %s", order_id)
            await asyncio.sleep(3)
        else:
            # Попробуем найти любую кнопку "Подтвердить" / "Да" в оверлее
            modal_yes = page.locator(
                '[class*="Modal"] button:has-text("Подтвердить"), '
                '[class*="Modal"] button:has-text("Да"), '
                '[class*="Overlay"] ~ * button:has-text("Подтвердить"), '
                '[class*="dialog"] button:has-text("Подтвердить")'
            )
            if await modal_yes.count() > 0:
                await modal_yes.first.click()
                await asyncio.sleep(3)
            else:
                # Последняя попытка: вторая кнопка "Подтвердить" на странице
                all_confirm = page.locator('button:has-text("Подтвердить")')
                count = await all_confirm.count()
                if count > 1:
                    await all_confirm.nth(1).click()
                    await asyncio.sleep(3)

        # Убедимся что модалка закрылась
        await _dismiss_any_overlay(page)

        logger.info("Заказ %s подтверждён (нажата 'Подтвердить')", order_id)
        return True

    except Exception as e:
        logger.error("Ошибка подтверждения заказа %s: %s", order_id, e)
        return False


async def _dismiss_any_overlay(page: Page) -> None:
    """Закрыть модальные окна/оверлеи если есть."""
    try:
        overlay = page.locator('[class*="Overlay"]')
        if await overlay.count() > 0:
            # Попробуем нажать Escape
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
    except Exception:
        pass


async def cancel_order(page: Page, order_id: str) -> bool:
    """Отменить заказ — нажать кнопку «Отменить» на странице заказа.

    После нажатия появляется модальное окно подтверждения —
    нужно подтвердить отмену.

    Returns True если отмена прошла успешно.
    """
    try:
        await _ensure_order_page(page, order_id)
        await asyncio.sleep(2)

        # Ищем кнопку "Отменить" / "Отказаться от заказа"
        cancel_btn = page.locator(
            'button:has-text("Отменить"), '
            'button:has-text("Отказаться"), '
            'button:has-text("Отказаться от заказа")'
        )
        if await cancel_btn.count() == 0:
            logger.warning("Кнопка 'Отменить' не найдена для заказа %s", order_id)
            return False

        # Используем force=True — оверлеи могут блокировать клик
        await cancel_btn.first.click(force=True)
        await asyncio.sleep(2)

        # Подтверждение в модальном окне
        modal_confirm = page.locator(
            '[data-testid*="alertModal"] button:has-text("Подтвердить"), '
            '[data-testid*="alertModal"] button:has-text("Да"), '
            '[class*="Modal"] button:has-text("Подтвердить"), '
            '[class*="Modal"] button:has-text("Да")'
        )
        if await modal_confirm.count() > 0:
            await modal_confirm.first.click(force=True)
            logger.info("Подтверждена отмена заказа %s в модалке", order_id)
            await asyncio.sleep(3)
        else:
            # Fallback: ищем любую кнопку подтверждения
            any_confirm = page.locator('button:has-text("Подтвердить"), button:has-text("Да")')
            count = await any_confirm.count()
            if count > 0:
                await any_confirm.first.click(force=True)
                await asyncio.sleep(3)

        await _dismiss_any_overlay(page)

        logger.info("Заказ %s отменён (нажата 'Отменить')", order_id)
        return True

    except Exception as e:
        logger.error("Ошибка отмены заказа %s: %s", order_id, e)
        return False


async def download_chat_files(page: Page, order_id: str, file_urls: list[str]) -> list[str]:
    """Скачать файлы из чата (прикреплённые заказчиком).

    Returns:
        Список путей к скачанным файлам.
    """
    from src.scraper.file_handler import download_files

    if not file_urls:
        return []

    try:
        downloaded = await download_files(page, order_id, file_urls)
        paths = [str(p) for p in downloaded]
        if paths:
            logger.info(
                "Скачано %d файлов из чата заказа %s",
                len(paths), order_id,
            )
        return paths
    except Exception as e:
        logger.warning("Ошибка скачивания файлов из чата %s: %s", order_id, e)
        return []


async def send_file_with_message(
    page: Page, order_id: str, filepath: str, message: str,
    variant: str = "final",
) -> bool:
    """Загрузить файл через 'Загрузить работу' и отправить сопроводительное сообщение.

    Файл загружается через штатную кнопку (Промежуточный/Окончательный),
    затем отдельно отправляется текстовое сообщение в чат.
    """
    from pathlib import Path
    from src.scraper.file_handler import upload_file

    file_ok = await upload_file(page, order_id, Path(filepath), variant=variant)
    if not file_ok:
        return False

    # Отправляем сопроводительное сообщение отдельно
    await asyncio.sleep(2)
    msg_ok = await send_message(page, order_id, message)
    return msg_ok
