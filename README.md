# Avtor24 AI Bot

Полностью автоматизированная система для работы на платформе avtor24.ru.

## Возможности

- Мониторинг и парсинг новых заказов
- AI-скоринг заказов (GPT-4o-mini)
- Автоматическая постановка ставок
- Генерация работ всех типов (эссе, рефераты, курсовые, ВКР, код и др.)
- Проверка антиплагиата (text.ru, ETXT)
- Авторерайт при низкой уникальности
- AI-чат с заказчиком
- Песочница для кода (Python, JS, Java, C++)
- Веб-дашборд с аналитикой и реалтайм-уведомлениями

## Стек

- **Backend:** Python 3.12, FastAPI, APScheduler
- **Scraping:** Playwright (Chromium)
- **AI:** OpenAI API (GPT-4o / GPT-4o-mini)
- **БД:** PostgreSQL + SQLAlchemy (async) + Alembic
- **Документы:** docx-js (Node.js)
- **Frontend:** HTML5, Tailwind CSS, Alpine.js, Chart.js
- **Деплой:** Docker, Railway

## Запуск локально

### 1. Клонировать и установить зависимости

```bash
git clone <repo-url>
cd avtor24-bot

pip install -r requirements.txt
npm install
playwright install chromium
```

### 2. Настроить переменные окружения

```bash
cp .env.example .env
# Заполнить .env своими ключами и учётными данными
```

### 3. Запустить БД и миграции

```bash
# PostgreSQL должен быть запущен
# Или использовать SQLite для разработки (по умолчанию в config.py)
python -m alembic upgrade head
```

### 4. Запустить приложение

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Дашборд: http://localhost:8000/dashboard/

### 5. Запустить тесты

```bash
pytest tests/ -v
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `AVTOR24_EMAIL` | Email для входа на avtor24.ru | — |
| `AVTOR24_PASSWORD` | Пароль от аккаунта | — |
| `AVTOR24_BASE_URL` | Базовый URL | `https://avtor24.ru` |
| `OPENAI_API_KEY` | Ключ OpenAI API | — |
| `OPENAI_MODEL_MAIN` | Модель для генерации | `gpt-4o` |
| `OPENAI_MODEL_FAST` | Модель для анализа/чата | `gpt-4o-mini` |
| `TEXTRU_API_KEY` | Ключ text.ru API | — |
| `ETXT_API_KEY` | Ключ ETXT API | — |
| `MIN_UNIQUENESS` | Мин. порог уникальности (%) | `50` |
| `DASHBOARD_USERNAME` | Логин дашборда | `admin` |
| `DASHBOARD_PASSWORD_HASH` | bcrypt-хеш пароля | — |
| `DASHBOARD_SECRET_KEY` | Секрет для JWT | — |
| `DATABASE_URL` | URL PostgreSQL | `sqlite+aiosqlite:///./avtor24.db` |
| `PROXY_RU` | Российский прокси (socks5) | — |
| `MAX_CONCURRENT_ORDERS` | Макс. заказов одновременно | `5` |
| `AUTO_BID` | Автоматические ставки | `true` |
| `MIN_PRICE_RUB` | Мин. цена заказа | `300` |
| `MAX_PRICE_RUB` | Макс. цена заказа | `50000` |
| `SCAN_INTERVAL_SECONDS` | Интервал сканирования | `60` |
| `SPEED_LIMIT_MIN_DELAY` | Мин. задержка между действиями (сек) | `30` |
| `SPEED_LIMIT_MAX_DELAY` | Макс. задержка (сек) | `120` |

## Деплой на Railway

### 1. Создать проект на Railway

- Подключить GitHub-репозиторий
- Добавить PostgreSQL плагин

### 2. Установить переменные окружения

В настройках Railway-сервиса добавить все переменные из таблицы выше.

`DATABASE_URL` будет автоматически предоставлен плагином PostgreSQL.

### 3. Деплой

Railway автоматически соберёт Docker-образ и запустит приложение.

При каждом push в main:
1. Docker build
2. `alembic upgrade head` (миграции)
3. `uvicorn src.main:app` (сервер)

### Healthcheck

```
GET /health → {"status": "ok", "uptime": ..., "bot_running": true}
```

## Антибан

- Случайные задержки между всеми действиями (30-120 сек)
- Ротация User-Agent (7 вариантов Chrome)
- Российский резидентный прокси
- Имитация человеческого поведения (плавное движение мыши, посимвольный ввод)
- Лимит: не более 20 ставок в день
- Мониторинг 403/captcha → автоматическая пауза 30 мин + уведомление
- Retry с экспоненциальным backoff (3 попытки)
- Graceful shutdown (завершение текущих задач перед остановкой)

## Структура проекта

```
src/
├── main.py              — FastAPI + APScheduler оркестратор
├── config.py            — Конфигурация из .env
├── ai_client.py         — OpenAI API обёртка с трекингом токенов
├── scraper/             — Парсинг Автор24 (Playwright)
├── analyzer/            — AI-скоринг + расчёт цен
├── generator/           — Генерация работ (15+ типов)
├── docgen/              — Сборка DOCX по ГОСТу
├── antiplagiat/         — Проверка уникальности + рерайт
├── chat_ai/             — AI-ответы заказчикам
├── sandbox/             — Песочница для кода
├── notifications/       — WebSocket уведомления
├── dashboard/           — Веб-дашборд (API + SPA)
└── database/            — SQLAlchemy ORM + CRUD
```

## Лицензия

Приватный проект. Все права защищены.
