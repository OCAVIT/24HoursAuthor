# ИНСТРУКЦИЯ ДЛЯ CLAUDE CODE (Opus 4.6)
# Проект: Avtor24 AI Bot
# Выполняй шаги СТРОГО ПО ПОРЯДКУ. Не пропускай ни одного.

---

## ШАГ 1: Инициализация проекта

Прочитай файл CLAUDE.md полностью. Это архитектура всего проекта.

Создай структуру папок из Секции 1 (только папки и __init__.py, без логики).
Создай файлы:
- `.env.example` по Секции 2
- `.gitignore` (Python, Node, .env, __pycache__, .playwright, cookies.json)
- `requirements.txt` по Секции 17
- `package.json` по Секции 17
- `railway.toml` по Секции 14
- `Dockerfile` по Секции 14
- `src/config.py` — pydantic-settings, загрузка всех переменных из .env

Инициализируй git: `git init && git add . && git commit -m "init: project structure"`

Установи зависимости: `pip install -r requirements.txt && npm install`

---

## ШАГ 2: База данных + Миграции

Прочитай Секцию 12 из CLAUDE.md.

Создай `src/database/models.py` — все таблицы из Секции 12 (orders, messages, action_logs, daily_stats, notifications, bot_settings, api_usage) через SQLAlchemy ORM (declarative base, async).

Создай `src/database/connection.py` — async engine + async sessionmaker, читает DATABASE_URL из config.

Создай `src/database/crud.py` — базовые CRUD операции: create_order, get_order, update_order_status, create_notification, get_notifications, create_action_log, track_api_usage, get_daily_stats.

Инициализируй Alembic:
```
alembic init alembic
```

Настрой `alembic/env.py`:
- Импортируй все модели из src/database/models.py
- Читай DATABASE_URL из переменной окружения
- Поддержка async (asyncpg)

Настрой `alembic.ini` — sqlalchemy.url пустой (берётся из env.py).

Сгенерируй первую миграцию:
```
alembic revision --autogenerate -m "initial tables"
```

Создай `tests/conftest.py` — фикстуры: тестовая SQLite in-memory БД, async session.
Создай `tests/test_database.py` — тесты: миграции применяются, CRUD работает.

Запусти: `pytest tests/test_database.py -v`
Всё должно быть зелёным.

`git add . && git commit -m "feat: database models + alembic migrations + tests"`

---

## ШАГ 3: FastAPI скелет + WebSocket

Прочитай Секции 10 и 13 из CLAUDE.md.

Создай `src/main.py`:
- FastAPI app
- GET `/health` → {"status": "ok", "uptime": ...}
- WebSocket `/ws/notifications` — принимает подключения, хранит в set
- WebSocket `/ws/logs` — аналогично
- Lifespan: при старте запускает alembic upgrade head программно
- Подключи роутеры (пока пустые заглушки):
  - `/api/dashboard/` 
  - `/dashboard/`

Создай `src/notifications/websocket.py`:
- ConnectionManager класс
- Методы: connect, disconnect, broadcast, send_personal
- Два менеджера: notification_manager, log_manager

Создай `src/notifications/events.py`:
- async def push_notification(type, order_id, title, body) → сохраняет в БД + broadcast через WebSocket

Тест: `tests/test_dashboard.py`:
- test_health_endpoint() — GET /health → 200
- test_websocket_connection() — WS подключение → работает

`pytest tests/ -v` — всё зелёное.

`git add . && git commit -m "feat: fastapi skeleton + websocket + health"`

---

## ШАГ 4: Скрапер — Browser Manager + Авторизация

Прочитай Секцию 3 (3.1 и 3.2) из CLAUDE.md.

Создай `src/scraper/browser.py`:
- Playwright async chromium
- Headless=True
- Прокси из config (PROXY_RU)
- User-Agent ротация (захардкодь 5-7 реальных Chrome UA)
- Случайные задержки между действиями (config.SPEED_LIMIT_MIN_DELAY, MAX_DELAY)
- Метод random_delay() — asyncio.sleep между min и max
- Singleton паттерн (один браузер на приложение)

Создай `src/scraper/auth.py`:
- Сохранение cookies в файл (cookies.json)
- Загрузка cookies при старте
- Проверка валидности сессии (заход на главную, проверка что залогинен)
- Логин через email/password если сессия невалидна
- Периодическое обновление сессии

Тест: `tests/test_scraper.py`:
- test_browser_init() — браузер создаётся (мок Playwright)
- test_random_delay() — задержка в пределах min/max
- test_session_management() — cookies сохраняются и загружаются

`pytest tests/ -v`

`git add . && git commit -m "feat: playwright browser manager + auth"`

---

## ШАГ 5: Скрапер — Парсинг заказов

Прочитай Секцию 3 (3.3 и 3.4) из CLAUDE.md.

Создай `src/scraper/orders.py`:
- async def fetch_order_list() → List[OrderSummary]
- Парсит https://avtor24.ru/order/search
- Извлекает все поля из Секции 3.3
- Пагинация: первые 3 страницы
- Дедупликация по order_id через БД

Создай `src/scraper/order_detail.py`:
- async def fetch_order_detail(order_url) → OrderDetail
- Парсит полную страницу заказа
- Извлекает все поля из Секции 3.4
- Скачивание прикреплённых файлов

Создай `src/scraper/file_handler.py`:
- async def download_files(order_id, file_urls) → List[filepath]
- async def upload_file(order_id, filepath) → bool
- Сохранение в /tmp/orders/{order_id}/

Для тестов создай папку `tests/fixtures/` и положи туда:
- `order_list.html` — захардкоженный HTML ленты заказов (скопируй структуру из скриншотов, 3-5 заказов)
- `order_detail.html` — захардкоженный HTML страницы заказа

Тесты: test_scraper.py:
- test_parse_order_list() — парсинг мок HTML → корректный список заказов
- test_parse_order_detail() — парсинг мок HTML → все поля заполнены
- test_deduplication() — повторный парсинг не дублирует заказы в БД

`pytest tests/ -v`

`git add . && git commit -m "feat: order list + detail parser"`

---

## ШАГ 6: Скрапер — Ставки + Чат

Прочитай Секцию 3 (3.5, 3.6, 3.7) из CLAUDE.md.

Создай `src/scraper/bidder.py`:
- async def place_bid(order_id, price, comment) → bool
- Заполнение формы ставки через Playwright
- Сохранение в БД: status='bid_placed'

Создай `src/scraper/chat.py`:
- async def get_new_messages() → List[Message]
- async def send_message(order_id, text) → bool
- async def send_file(order_id, filepath) → bool
- Мониторинг вкладки "Мои заказы" на новые сообщения

Тесты:
- test_bid_placement() — мок формы, ставка "отправляется"
- test_chat_send() — мок чата, сообщение "отправляется"

`pytest tests/ -v`

`git add . && git commit -m "feat: bidder + chat scraper"`

---

## ШАГ 7: Анализатор заказов

Прочитай Секцию 4 из CLAUDE.md.

Создай `src/analyzer/order_scorer.py`:
- async def score_order(order: OrderDetail) → ScoreResult
- Вызов GPT-4o-mini с промптом из Секции 4.1
- Возвращает JSON: {score, can_do, estimated_time_min, estimated_cost_rub, reason}
- Трекинг использованных токенов в api_usage

Создай `src/analyzer/price_calculator.py`:
- async def calculate_price(order, score) → int (рубли)
- Логика из Секции 4.2

Создай `src/analyzer/file_analyzer.py`:
- async def analyze_files(filepaths) → str (суммари методички)
- PDF → PyMuPDF → текст → GPT-4o-mini суммари
- DOCX → python-docx → текст → GPT-4o-mini суммари

Тесты: `tests/test_analyzer.py`:
- test_scoring() — мок OpenAI → возвращает валидный JSON со score
- test_price_budget() — заказ с бюджетом 3000 → ставка ~2550-2850
- test_price_no_budget() — договорная цена → расчёт по формуле
- test_file_analysis() — мок PDF → суммари извлекается

`pytest tests/ -v`

`git add . && git commit -m "feat: order scorer + price calculator + file analyzer"`

---

## ШАГ 8: Генератор работ — MVP (эссе + реферат)

Прочитай Секции 5 (5.1, 5.2) и 6 из CLAUDE.md.

Создай промпты:
- `src/generator/prompts/essay_system.txt` — системный промпт для эссе
- `src/generator/prompts/referat_system.txt` — системный промпт для рефератов

Создай `src/generator/essay.py`:
- async def generate(order: OrderDetail) → GenerationResult
- Один вызов GPT-4o → текст эссе
- Учитывает: тему, предмет, кол-во страниц, описание заказчика, методичку

Создай `src/generator/referat.py`:
- async def generate(order) → GenerationResult
- Шаг 1: GPT-4o генерирует план (JSON)
- Шаг 2: Введение
- Шаг 3: Каждый раздел отдельным вызовом
- Шаг 4: Заключение
- Шаг 5: Библиография

Создай `src/generator/router.py` — маппинг из Секции 5.1 (пока только эссе и реферат активны, остальные → заглушки).

Создай `src/docgen/builder.py`:
- Вызывает Node.js скрипт через subprocess
- Передаёт JSON с содержимым
- Получает путь к готовому DOCX

Создай `scripts/generate_docx.js`:
- Используй библиотеку `docx` (docx-js)
- Читает JSON из stdin
- Формирует DOCX по ГОСТу (Секция 6):
  - Times New Roman, 14pt, интервал 1.5
  - Поля: лево 3см, право 1.5см, верх/низ 2см
  - Абзацный отступ 1.25см
  - Выравнивание по ширине
  - Нумерация страниц (со 2-й)
  - Титульный лист
  - Содержание (автогенерируемое)
  - Заголовки жирные
- Записывает .docx файл, выводит путь в stdout

Тесты: `tests/test_generator.py`:
- test_essay_generation() — мок OpenAI → текст генерируется → DOCX создаётся
- test_referat_plan() — мок OpenAI → план в JSON формате
- test_docx_valid() — сгенерированный .docx открывается python-docx без ошибок
- test_router_mapping() — все типы работ имеют маппинг

`pytest tests/ -v`

`git add . && git commit -m "feat: essay + referat generators + DOCX builder"`

---

## ШАГ 9: Антиплагиат

Прочитай Секцию 7 из CLAUDE.md.

Создай `src/antiplagiat/textru.py`:
- async def check(text: str) → float (процент уникальности)
- API text.ru: POST текст → получить uid → poll результат

Создай `src/antiplagiat/etxt.py`:
- async def check(text: str) → float
- API ETXT (если ключ есть)

Создай `src/antiplagiat/checker.py`:
- async def check_uniqueness(filepath, system="textru") → CheckResult
- Извлекает текст из DOCX → отправляет в нужный сервис

Создай `src/antiplagiat/rewriter.py`:
- async def rewrite_for_uniqueness(text, target_percent) → str
- GPT-4o перефразирует абзацы
- Промпт из Секции 7.2

Создай `src/generator/router.py` (обнови):
- После генерации → checker.check_uniqueness()
- Если < порога → rewriter → повторная проверка (до 3 раз)

Тесты: `tests/test_antiplagiat.py`:
- test_check_uniqueness() — мок API → возвращает процент
- test_rewriter() — мок OpenAI → текст перефразирован (отличается от оригинала)
- test_rewrite_loop() — 3 итерации максимум

`pytest tests/ -v`

`git add . && git commit -m "feat: antiplagiat checker + rewriter"`

---

## ШАГ 10: AI-чат с заказчиком

Прочитай Секцию 9 из CLAUDE.md.

Создай `src/chat_ai/prompts/chat_system.txt` — системный промпт из Секции 9.

Создай `src/chat_ai/responder.py`:
- async def generate_response(order, message_history, new_message) → str
- GPT-4o-mini с системным промптом
- Контекст: описание заказа + вся история + статус
- Ответ: максимум 2-3 предложения
- Трекинг токенов в api_usage

Тесты: `tests/test_chat_ai.py`:
- test_response_generation() — мок OpenAI → ответ генерируется
- test_no_ai_mention() — ответ не содержит слов "AI", "нейросеть", "ChatGPT", "GPT", "искусственный интеллект"
- test_response_length() — ответ ≤ 3 предложений
- test_context_included() — в промпт передаётся описание заказа

`pytest tests/ -v`

`git add . && git commit -m "feat: AI chat responder"`

---

## ШАГ 11: Главный цикл (оркестратор)

Прочитай Секцию 13 из CLAUDE.md.

Обнови `src/main.py`:
- APScheduler с 4 задачами:
  1. scan_orders_job — каждые SCAN_INTERVAL_SECONDS
  2. process_accepted_orders_job — каждые 120 сек
  3. chat_responder_job — каждые 120 сек
  4. daily_summary_job — в 22:00

Реализуй полный флоу из Секции 13:
- Сканирование → скоринг → ставка → уведомление
- Принятие → генерация → антиплагиат → доставка → уведомление
- Новое сообщение → AI ответ → уведомление
- Все действия логируются в action_logs
- Все уведомления пушатся через WebSocket

Обработка ошибок:
- try/except на каждом заказе (один сбой не ломает весь цикл)
- Ошибки → notification type='error' + action_log
- Retry 3 раза с экспоненциальным backoff для сетевых ошибок

`git add . && git commit -m "feat: main orchestrator loop"`

---

## ШАГ 12: Расширенные генераторы

Прочитай Секцию 5 (5.3, 5.4) из CLAUDE.md.

Создай генераторы для ВСЕХ типов работ:

`src/generator/coursework.py` — курсовая работа (Секция 5.3):
- Пошаговая генерация: план → введение → главы → заключение → библиография
- Каждая глава — отдельный API вызов
- Суммари предыдущих глав передаётся в контекст

`src/generator/diploma.py` — ВКР/дипломная (Секция 5.4):
- Аналогично курсовой, но 80-100 стр, 3-4 главы по 15-25 стр
- Титульный лист, задание на ВКР, аннотация, содержание, приложения
- Библиография 30-50 источников

`src/generator/homework.py` — контрольные, решение задач, ответы на вопросы, лабораторные

`src/generator/presentation.py` — генерация текста для слайдов (PPTX или текст + инструкция)

`src/generator/translation.py` — перевод текста

`src/generator/copywriting.py` — копирайтинг, рерайт, набор текста

`src/generator/business_plan.py` — бизнес-планы

`src/generator/practice_report.py` — отчёты по практике

`src/generator/review.py` — рецензии, вычитка

`src/generator/uniqueness.py` — повышение уникальности (рерайт входного текста)

Для каждого создай системный промпт в `src/generator/prompts/`.

Обнови `src/generator/router.py` — все маппинги из Секции 5.1 теперь активны.

Обнови `scripts/generate_docx.js` — шаблоны DOCX для разных типов работ (титульные листы различаются).

Тесты для каждого генератора (мок OpenAI).

`pytest tests/ -v`

`git add . && git commit -m "feat: all work type generators"`

---

## ШАГ 13: Песочница для кода

Прочитай Секции 5.5 и 8 из CLAUDE.md.

Создай `src/sandbox/executor.py`:
- async def execute_code(code, language, stdin, timeout=30) → ExecutionResult
- Запуск через subprocess (docker run) или напрямую в изолированном процессе
- Ограничения: таймаут 30с, память 256MB
- Поддержка: Python, JavaScript, Java, C++, C#

Создай `src/sandbox/languages.py`:
- Конфиг для каждого языка: расширение файла, команда компиляции, команда запуска

Создай `src/generator/code_task.py` (Секция 5.5):
- async def generate(order) → GenerationResult
- Цикл: GPT-4o генерирует код → sandbox запускает → если ошибка → stderr обратно в GPT → повтор до 5 раз
- Если тесты от заказчика есть → прогнать их тоже

Тесты: `tests/test_sandbox.py`:
- test_python_execution() — print("hello") → stdout="hello\n"
- test_python_error() — syntax error → stderr содержит traceback
- test_timeout() — while True → таймаут
- test_code_fix_loop() — мок: первая попытка ошибка, вторая успех

`pytest tests/ -v`

`git add . && git commit -m "feat: code sandbox + AI fix loop"`

---

## ШАГ 14: Дашборд — Backend API

Прочитай Секцию 11 из CLAUDE.md.

Создай `src/dashboard/auth.py`:
- POST /api/dashboard/login → JWT токен (httpOnly cookie)
- Middleware: проверка JWT на всех /api/dashboard/* и /dashboard/* (кроме /login)
- DASHBOARD_USERNAME + DASHBOARD_PASSWORD_HASH из env

Создай `src/dashboard/app.py` — FastAPI роутер со ВСЕМИ эндпоинтами из Секции 13:
```
GET  /api/dashboard/stats             — виджеты: баланс, доходы, активные заказы, API расход
GET  /api/dashboard/orders            — список заказов (пагинация, фильтры, сортировка)
GET  /api/dashboard/orders/{id}       — детали заказа + чат + логи + API usage
GET  /api/dashboard/analytics         — аналитика по периоду (доход, заказы, токены, ROI)
GET  /api/dashboard/notifications     — список уведомлений
POST /api/dashboard/notifications/read — пометить прочитанными
GET  /api/dashboard/logs              — логи действий бота
GET  /api/dashboard/settings          — текущие настройки
PUT  /api/dashboard/settings          — обновить настройки
POST /api/dashboard/orders/{id}/stop  — остановить обработку
POST /api/dashboard/orders/{id}/regen — перегенерировать работу
POST /api/dashboard/chat/{id}/send    — ручное сообщение в чат (override AI)
GET  /api/dashboard/export/csv        — экспорт заказов в CSV
```

Каждый эндпоинт берёт данные из БД через crud.py.

Настройки бота: читаются из таблицы bot_settings, UI может их менять. При старте — дефолтные значения из .env вставляются в bot_settings если их там нет.

Тесты: `tests/test_dashboard.py`:
- test_login_success() — правильный пароль → 200 + cookie
- test_login_fail() — неправильный → 401
- test_unauthorized() — без cookie → 401
- test_stats() — /api/dashboard/stats → JSON с нужными полями
- test_orders_list() — /api/dashboard/orders → пагинированный список

`pytest tests/ -v`

`git add . && git commit -m "feat: dashboard backend API"`

---

## ШАГ 15: Дашборд — Frontend

Прочитай Секцию 11 из CLAUDE.md (все 8 страниц).

Создай SPA фронтенд в `src/dashboard/static/`:

`index.html` — основной layout:
- Sidebar навигация (Overview, Заказы, Аналитика, Уведомления, Логи, Настройки, Чаты)
- Верхняя панель: колокольчик, статус бота, uptime, кнопка стоп/старт
- Контентная область (динамическая)

`login.html` — страница логина

`styles.css` — Tailwind CSS (CDN)

`app.js` — основная логика:
- Alpine.js для реактивности
- WebSocket подключение к /ws/notifications и /ws/logs
- Fetch к /api/dashboard/* для данных
- Chart.js для графиков
- Роутинг между страницами (hash-based: #overview, #orders, #analytics, etc.)
- Звуковое оповещение при новом уведомлении
- Browser push-notification запрос

Страницы (всё из Секции 11):
1. Overview — виджеты-карточки + графики доходов + лента событий (реалтайм)
2. Заказы — таблица с вкладками + фильтры + детальная карточка по клику
3. Аналитика — графики доходов, заказов, токенов, ROI, антиплагиат статистика
4. Уведомления — лента + фильтры + пометить прочитанными
5. Логи — реалтайм терминал через WebSocket
6. Настройки — формы для всех параметров из Секции 11.7
7. Чаты — мессенджер-интерфейс (список слева, переписка справа)

FastAPI отдаёт HTML:
- GET /dashboard/ → index.html (если авторизован)
- GET /dashboard/login → login.html

Дизайн:
- Тёмная тема (dark mode) — основная
- Акцентный цвет: фиолетовый (#7C3AED) как у Автор24
- Округлые карточки, тени
- Адаптив под мобилку (sidebar → бургер)

`git add . && git commit -m "feat: dashboard frontend SPA"`

---

## ШАГ 16: Интеграционное тестирование

Запусти ВСЕ тесты:
```
pytest tests/ -v --tb=long
```

Исправь все падающие тесты.

Проверь что полный цикл работает (мок-тест):
1. Мок HTML ленты заказов → парсится → заказ скорится → ставка "ставится"
2. Мок принятого заказа → генерируется эссе → DOCX создаётся → антиплагиат "проходит"
3. WebSocket → уведомление приходит
4. Dashboard API → все эндпоинты отвечают корректно

`git add . && git commit -m "test: full integration tests"`

---

## ШАГ 17: Финальная полировка

Прочитай Секцию 15 из CLAUDE.md (антибан).

Реализуй:
- Случайные задержки между ВСЕМИ действиями скрапера (не просто sleep, а random между min и max)
- Не более 15-20 ставок в день (счётчик в daily_stats)
- Мониторинг бана: если 403/captcha → пауза 30 мин → уведомление на дашборд
- Retry логика: 3 попытки с exponential backoff для сетевых ошибок
- Graceful shutdown: при остановке — завершить текущие задачи, не обрывать

Обнови Dockerfile — production CMD (без тестов):
```
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

Создай `README.md`:
- Описание проекта
- Инструкция по деплою на Railway
- Список переменных окружения
- Как запустить локально

`git add . && git commit -m "feat: anti-ban + retry + polish + README"`

---

## ШАГ 18: Деплой на Railway

Проверь что все файлы на месте:
```
ls -la Dockerfile railway.toml requirements.txt package.json src/ tests/ alembic/
```

Проверь что Dockerfile собирается:
```
docker build -t avtor24-bot .
```

Проверь что тесты проходят:
```
pytest tests/ -v
```

Всё готово для `railway up` или push в GitHub → Railway auto-deploy.

`git add . && git commit -m "release: v1.0.0 ready for production"`

---

# ВАЖНЫЕ ПРАВИЛА ДЛЯ CLAUDE CODE:

1. После КАЖДОГО шага запускай `pytest tests/ -v` и убеждайся что всё зелёное
2. После каждого шага делай git commit
3. Если тест падает — СНАЧАЛА исправь тест, потом продолжай
4. Все API вызовы к OpenAI оборачивай в try/except + трекинг токенов в api_usage
5. Все действия скрапера оборачивай в try/except + запись в action_logs
6. Мок OpenAI в тестах — НЕ тратить реальные токены
7. Мок Playwright в тестах — НЕ ходить на реальный avtor24.ru
8. Все секреты только через переменные окружения, никогда не хардкодь
9. Используй async/await везде где возможно
10. Docstrings на каждой функции (кратко, на русском)
