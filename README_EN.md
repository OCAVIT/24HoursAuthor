<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white" />
  <img src="https://img.shields.io/badge/Playwright-1.50-2EAD33?style=for-the-badge&logo=playwright&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
</p>

<p align="center">
  <b>🇷🇺 <a href="README.md">Русский</a></b> · <b>🇬🇧 <a href="#en">English</a></b>
</p>

---

<a id="en"></a>

# Avtor24 AI Bot — Full Freelance Platform Automation

> An autonomous system that discovers orders, evaluates them, places bids, generates academic papers using GPT-4o, runs plagiarism checks, delivers to clients, and manages conversations — **fully hands-free**.

## Table of Contents

- [Overview](#overview)
- [Key Metrics](#key-metrics)
- [Architecture](#architecture)
- [Order Processing Pipeline](#order-processing-pipeline)
- [Tech Stack](#tech-stack)
- [Core Modules](#core-modules)
- [Web Dashboard](#web-dashboard)
- [Work Generation](#work-generation)
- [Plagiarism Checking](#plagiarism-checking)
- [Code Sandbox](#code-sandbox)
- [Anti-Ban System](#anti-ban-system)
- [Testing](#testing)
- [Getting Started](#getting-started)
- [Deployment](#deployment)
- [Project Structure](#project-structure)

---

## Overview

**Avtor24 AI Bot** is a production-ready system that automates the entire workflow of a freelance author on avtor24.ru (Russia's largest academic freelance platform). The bot operates 24/7, autonomously handling orders from discovery to final delivery.

### What the system does:

1. **Scans** the order feed on avtor24.ru every 60 seconds
2. **Evaluates** each order via GPT-4o-mini (scoring 0–100)
3. **Places bids** with AI-generated personalized comments
4. **Generates academic papers** of any type (essays, coursework, theses, code, etc.)
5. **Checks plagiarism** and performs AI-powered rewriting if needed
6. **Produces DOCX files** compliant with Russian academic standards (GOST)
7. **Delivers work** to clients in two stages (draft → final)
8. **Manages conversations** with clients as a human author
9. **Provides a dashboard** with analytics, notifications, and full bot control

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Source code (Python) | **9,100+ lines** |
| Test code | **5,590 lines** |
| Total volume (incl. Node.js, HTML) | **15,000+ lines** |
| Python modules | 46 files |
| Supported work types | 15+ |
| System prompts | 13 |
| Database tables | 8 |
| Dashboard pages | 8 |
| API endpoints | 30+ |
| WebSocket streams | 2 (notifications, logs) |
| Test modules | 9 files |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AVTOR24 AI BOT                           │
├─────────────┬──────────────┬──────────────┬─────────────────────┤
│  SCRAPER    │  ANALYZER    │  GENERATOR   │  DASHBOARD          │
│             │              │              │                     │
│ Playwright  │ GPT-4o-mini  │ GPT-4o       │ FastAPI + Alpine.js │
│ + RU Proxy  │ Scoring      │ 15+ types    │ WebSocket realtime  │
│ + Anti-ban  │ Pricing      │ Stepwise     │ Chart.js analytics  │
│ + Cookies   │ File Analysis│ GOST DOCX    │ JWT authentication  │
├─────────────┼──────────────┼──────────────┼─────────────────────┤
│ PLAGIARISM  │  CHAT AI     │  SANDBOX     │  NOTIFICATIONS      │
│             │              │              │                     │
│ text.ru API │ GPT-4o-mini  │ Docker       │ WebSocket push      │
│ ETXT API    │ Auto-replies │ Python/JS/   │ Sound + Push API    │
│ AI Rewriter │ Intent detect│ Java/C++/C#  │ Badge + bell icon   │
├─────────────┴──────────────┴──────────────┴─────────────────────┤
│                    DATABASE (PostgreSQL / SQLite)                │
│  Orders · Messages · ActionLogs · DailyStats · Notifications    │
│  BotSettings · ApiUsage                                         │
├─────────────────────────────────────────────────────────────────┤
│             DEPLOYMENT: Docker + Railway (auto-migrations)       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Order Processing Pipeline

```
 ① SCANNING               ② ANALYSIS              ③ BIDDING
 ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ Parse order  │───▶│ AI scoring       │───▶│ Calculate price   │
 │ feed every   │    │ (score ≥ 60)     │    │ + AI comment      │
 │ 60 seconds   │    │ Analyze files    │    │ Place bid         │
 └──────────────┘    └──────────────────┘    └──────────────────┘
                                                      │
 ┌─────────────────────────────────────────────────────┘
 ▼
 ④ GENERATION             ⑤ PLAGIARISM CHECK       ⑥ DOCX
 ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ Plan → Sections  │───▶│ Sample check     │───▶│ Title page       │
 │ → Expand         │    │ → Full check     │    │ Table of contents│
 │ to target volume │    │ Rewrite (up to 3)│    │ Times New Roman  │
 └──────────────────┘    └──────────────────┘    │ GOST formatting  │
                                                  └──────────────────┘
                                                          │
 ┌────────────────────────────────────────────────────────┘
 ▼
 ⑦ DRAFT DELIVERY         ⑧ APPROVAL              ⑨ FINAL DELIVERY
 ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ Upload file      │───▶│ AI detects       │───▶│ Upload as Final  │
 │ + cover message  │    │ approval intent  │    │ + request review │
 │                  │    │ in client chat   │    │ from client      │
 └──────────────────┘    └──────────────────┘    └──────────────────┘
```

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Backend** | Python 3.12, FastAPI, APScheduler | Server, background tasks |
| **Web Scraping** | Playwright (Chromium) | Browser automation |
| **AI/ML** | OpenAI API (GPT-4o, GPT-4o-mini) | Generation, analysis, chat |
| **Database** | PostgreSQL + SQLAlchemy + Alembic | Data persistence, migrations |
| **Documents** | docx-js (Node.js 22) | GOST-compliant DOCX generation |
| **Frontend** | Alpine.js + Tailwind CSS + Chart.js | Interactive dashboard |
| **Real-time** | WebSocket (FastAPI) | Live notifications, logs |
| **Auth** | JWT + bcrypt + httpOnly cookies | Secure authentication |
| **Testing** | pytest + pytest-asyncio | 5,590 lines of tests |
| **Deploy** | Docker, docker-compose, Railway | Containerization, hosting |
| **Proxy** | SOCKS5 (Russian residential) | Geo-restriction bypass |

---

## Core Modules

### Scraper (`src/scraper/`) — 1,290 lines

Full automation of interactions with the avtor24.ru React SPA:

- **browser.py** — Playwright manager with proxy, User-Agent rotation (7 variants), viewport randomization, cookie persistence
- **auth.py** — Authentication with countrylock phone verification support
- **orders.py** — Order feed parsing (React SPA with `styled-components`)
- **order_detail.py** — Extraction of 15+ fields from order cards
- **bidder.py** — Bid placement with AI-generated comments
- **chat.py** — Read/send messages, two-stage delivery, approval intent detection
- **file_handler.py** — File upload (draft + final versions)
- **antiban.py** — Ban detection, daily bid limits, rate limiting

### Analyzer (`src/analyzer/`) — 670 lines

- **order_scorer.py** — AI order scoring (0–100, can_do, estimated_time, reason)
- **price_calculator.py** — Price calculation (budget-based / average bid / formula)
- **file_analyzer.py** — Text extraction from PDF/DOCX, methodology summarization
- **field_extractor.py** — Parsing formatting, structure, and special requirements

### Generator (`src/generator/`) — 1,440 lines

- **router.py** — Mapping 30+ work types to 15 generators
- **stepwise.py** — Universal engine: plan → sections → expand to target volume
- **Specialized generators:** essays, reports, coursework, theses, homework, code tasks, presentations, translations, copywriting, business plans, practice reports, reviews, uniqueness improvement
- **prompts/** — 13 system prompts per work type

### DOCX Builder (`src/docgen/`) — 270 lines

- Generation via Node.js subprocess (docx-js)
- Title page, auto-generated table of contents with page numbers
- Times New Roman 12–14pt, 1.5 line spacing, GOST margins
- HeadingLevel styles, page numbering, bibliography formatting

### Plagiarism Checker (`src/antiplagiat/`) — 610 lines

- **checker.py** — Optimized checking: sample-based → full only when needed
- **rewriter.py** — AI rewriting of low-uniqueness paragraphs (up to 3 iterations)
- **textru.py** — text.ru API integration
- **etxt.py** — ETXT API integration

### AI Chat (`src/chat_ai/`) — 400 lines

- Response generation as a human author (2–3 sentences, no AI mentions)
- Approval intent detection for two-stage delivery
- Context-aware replies (order status, deadline, requirements)

### Code Sandbox (`src/sandbox/`) — 330 lines

- Docker containers for code execution (Python, JS, Java, C++, C#)
- 30-second timeout, 256MB memory limit, no network, read-only filesystem
- AI error-fixing loop (up to 5 iterations)

---

## Web Dashboard

A full-featured SPA serving as the **sole control center** for the entire system.

### Pages:

| Page | Functionality |
|------|--------------|
| **Overview** | Income widgets (today/week/all-time), active orders, real-time activity feed, charts (Chart.js), bot status, Start/Stop button |
| **Orders** | Filterable/sortable/paginated table, color-coded status badges, detailed order card, regeneration, CSV export |
| **Analytics** | Income by day, conversion funnel, work type distribution, API token consumption, ROI tracking |
| **Notifications** | Real-time via WebSocket, bell icon with badge, sound alerts, browser push notifications |
| **Logs** | Real-time bot action log (terminal-style), filter by type, search, export |
| **Settings** | All bot parameters via UI: intervals, filters, pricing, limits, prompt templates |
| **Chats** | Messenger-style interface, AI response override, manual messages |

### Frontend technologies:

- **Alpine.js** — reactivity without heavy frameworks
- **Tailwind CSS** — utility-first styling
- **Chart.js** — charts and analytics
- **WebSocket** — instant updates without page reload

---

## Work Generation

### Stepwise Generation Algorithm:

```
1. PLANNING
   GPT-4o → JSON plan with sections and target word counts

2. SECTION-BY-SECTION GENERATION
   For each section → separate GPT-4o call with context:
   - Topic, subject, order description
   - Methodology guidelines (if attached)
   - Previous sections (summary)
   - Target section volume

3. EXPANSION
   If total volume < target:
   → GPT-4o expands the shortest sections

4. POST-PROCESSING
   - Markdown artifact cleanup
   - Duplicate header removal
   - Bibliography generation (GOST R 7.0.5-2008)
```

### Supported work types:

| Work Type | Default Volume | Notes |
|-----------|---------------|-------|
| Essay | 5 pages | Free-form structure, creative style |
| Report | 10–12 pages | Introduction + 2-3 chapters + conclusion |
| Coursework | 25 pages | 3 chapters with subsections, 15–25 bibliography entries |
| Thesis / Diploma | 60 pages | 3-4 chapters, 30–50 references, ~20-30 API calls |
| Homework | 8 pages | Q&A, problem solving |
| Code Task | — | Generation + sandbox execution + AI fix loop |
| Presentation | 15 slides | Structured slide content |
| Translation | 10 pages | EN→RU / RU→EN |
| Business Plan | 15 pages | Marketing, finance, SWOT analysis |
| Practice Report | 20 pages | Daily log, conclusions |

---

## Plagiarism Checking

### Optimized checking strategy:

```
1. SAMPLE CHECK (saves API credits)
   → 2-3 random excerpts of 1,500-2,000 characters
   → If average uniqueness ≥ threshold + 5% → ACCEPTED

2. FULL CHECK (if sample fails)
   → Entire text via text.ru / ETXT API

3. REWRITE (if uniqueness < threshold)
   → GPT-4o paraphrases borrowed paragraphs
   → Re-check
   → Up to 3 iterations
```

---

## Code Sandbox

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│   GPT-4o     │────▶│ Docker Container │────▶│   Result     │
│ generates    │     │ --memory=256m    │     │ stdout/stderr│
│ code         │     │ --cpus=1         │     │ exit_code    │
└──────────────┘     │ --network=none   │     └──────┬───────┘
       ▲             │ --read-only      │            │
       │             │ timeout=30s      │            │ Error?
       │             └─────────────────┘            │
       └────────────── GPT-4o fixes ◀───────────────┘
                      (up to 5 attempts)
```

**Languages:** Python 3.12 · JavaScript (Node.js 20) · Java 17 · C++ (g++ 13) · C# (.NET 8)

---

## Anti-Ban System

| Mechanism | Implementation |
|-----------|---------------|
| Delays | 30–120 sec between actions (randomized) |
| User-Agent | Rotation across 7 real Chrome UAs |
| Viewport | Randomized: 1920x1080, 1366x768, 1536x864 |
| Bid limit | Max 20 per day |
| Ban detection | Monitor 403/captcha → 30-min pause |
| Retry | Exponential backoff (3 attempts) |
| Proxy | Russian residential SOCKS5 |
| Behavior | Human-like actions (typing, scrolling) |
| Cookies | Persistent session (cookies.json) |

---

## Testing

**5,590 lines of test code** across 9 modules:

| Module | Lines | Coverage |
|--------|-------|----------|
| `test_integration.py` | 1,269 | Full end-to-end pipeline |
| `test_scraper.py` | 806 | Parsing with real HTML fixtures |
| `test_generator.py` | 778 | All 15+ generators |
| `test_dashboard.py` | 619 | All dashboard API endpoints |
| `test_analyzer.py` | 588 | Scoring, pricing, file analysis |
| `test_antiplagiat.py` | 525 | Uniqueness checking + rewriting |
| `test_sandbox.py` | 494 | Code execution in Docker |
| `test_chat_ai.py` | 320 | Response generation, intent detection |
| `test_database.py` | 161 | ORM, migrations, CRUD |

### Mock strategy:

- **OpenAI API** → fixed responses (no token spending)
- **Playwright** → real HTML fixtures from avtor24.ru
- **Plagiarism APIs** → fixed uniqueness percentages
- **PostgreSQL** → SQLite in-memory for speed

```bash
# Run all tests
pytest tests/ -v

# Run a specific module
pytest tests/test_generator.py -v

# With live API (real OpenAI calls)
pytest tests/ -v -m live_api
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker (for code sandbox)
- PostgreSQL (production) or SQLite (development)

### 1. Install dependencies

```bash
git clone <repo-url>
cd avtor24-bot

pip install -r requirements.txt
npm install
playwright install chromium
```

### 2. Configuration

```bash
cp .env.example .env
# Fill in .env with your keys
```

<details>
<summary>Environment variables</summary>

| Variable | Description | Default |
|----------|-------------|---------|
| `AVTOR24_EMAIL` | Login email for avtor24.ru | — |
| `AVTOR24_PASSWORD` | Account password | — |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `OPENAI_MODEL_MAIN` | Model for generation | `gpt-4o` |
| `OPENAI_MODEL_FAST` | Model for analysis/chat | `gpt-4o-mini` |
| `TEXTRU_API_KEY` | text.ru API key | — |
| `ETXT_API_KEY` | ETXT API key | — |
| `MIN_UNIQUENESS` | Min uniqueness threshold (%) | `50` |
| `DASHBOARD_USERNAME` | Dashboard login | `admin` |
| `DASHBOARD_PASSWORD_HASH` | bcrypt password hash | — |
| `DASHBOARD_SECRET_KEY` | JWT secret | — |
| `DATABASE_URL` | Database URL | `sqlite+aiosqlite:///./avtor24.db` |
| `PROXY_RU` | Russian proxy (socks5) | — |
| `MAX_CONCURRENT_ORDERS` | Max concurrent orders | `5` |
| `AUTO_BID` | Automatic bidding | `true` |
| `MIN_PRICE_RUB` / `MAX_PRICE_RUB` | Price range | `300` / `50000` |
| `SCAN_INTERVAL_SECONDS` | Scan interval | `60` |
| `SPEED_LIMIT_MIN_DELAY` / `MAX_DELAY` | Anti-ban delays | `30` / `120` |

</details>

### 3. Migrations and launch

```bash
# Run migrations
python -m alembic upgrade head

# Start server (dashboard + API + bot)
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# Or standalone monitoring (no dashboard)
python scripts/run_monitor.py
```

Dashboard: `http://localhost:8000/dashboard/`

### 4. Docker

```bash
docker-compose up -d
```

---

## Deployment

### Railway (production)

1. Connect your GitHub repository to Railway
2. Add the PostgreSQL plugin
3. Set environment variables
4. Railway automatically builds and runs:

```
Docker build → alembic upgrade head → uvicorn src.main:app
```

### Health check

```
GET /health → {"status": "ok", "uptime": 3600, "bot_running": true}
```

---

## Project Structure

```
avtor24-bot/
├── src/                              # Source code (9,100+ lines)
│   ├── main.py                       # FastAPI + APScheduler orchestrator
│   ├── config.py                     # Configuration from .env (Pydantic Settings)
│   ├── ai_client.py                  # OpenAI API wrapper with token tracking
│   ├── pipeline.py                   # Order processing pipeline
│   │
│   ├── scraper/                      # Avtor24 scraping (Playwright)
│   │   ├── browser.py                # Browser manager (proxy, UA, cookies)
│   │   ├── auth.py                   # Authentication + countrylock
│   │   ├── orders.py                 # Order feed parsing
│   │   ├── order_detail.py           # Order detail parsing
│   │   ├── bidder.py                 # Bid placement
│   │   ├── chat.py                   # Read/send messages + two-stage delivery
│   │   ├── file_handler.py           # File uploads
│   │   └── antiban.py                # Ban detection, rate limiting
│   │
│   ├── analyzer/                     # AI order analysis
│   │   ├── order_scorer.py           # Scoring (GPT-4o-mini)
│   │   ├── price_calculator.py       # Optimal price calculation
│   │   ├── file_analyzer.py          # PDF/DOCX text extraction
│   │   └── field_extractor.py        # Requirements parsing
│   │
│   ├── generator/                    # Work generation (15+ types)
│   │   ├── router.py                 # Work type router
│   │   ├── stepwise.py               # Stepwise generation engine
│   │   ├── essay.py ... review.py    # Specialized generators
│   │   └── prompts/                  # 13 system prompts
│   │
│   ├── docgen/                       # GOST-compliant DOCX generation
│   │   ├── builder.py                # Node.js subprocess (docx-js)
│   │   └── formatter.py              # Text normalization
│   │
│   ├── antiplagiat/                  # Plagiarism checking
│   │   ├── checker.py                # Optimized checker
│   │   ├── rewriter.py               # AI rewriter
│   │   ├── textru.py                 # text.ru API
│   │   └── etxt.py                   # ETXT API
│   │
│   ├── chat_ai/                      # AI client responses
│   │   └── responder.py              # Response generation + intent detection
│   │
│   ├── sandbox/                      # Code sandbox (Docker)
│   │   ├── executor.py               # Container-based code runner
│   │   └── languages.py              # Language configurations
│   │
│   ├── dashboard/                    # Web dashboard
│   │   ├── app.py                    # FastAPI router (30+ endpoints)
│   │   ├── auth.py                   # JWT authentication
│   │   └── static/                   # SPA (HTML + Tailwind + Alpine.js)
│   │
│   ├── notifications/                # Real-time notifications (WebSocket)
│   │   ├── websocket.py              # WebSocket manager
│   │   └── events.py                 # Event broadcasting
│   │
│   └── database/                     # PostgreSQL ORM
│       ├── models.py                 # SQLAlchemy models (8 tables)
│       ├── crud.py                   # CRUD + analytics queries
│       └── connection.py             # Database connection
│
├── scripts/
│   ├── generate_docx.js              # Node.js DOCX generation
│   └── run_monitor.py                # Standalone monitoring
│
├── tests/                            # Tests (5,590 lines)
│   ├── fixtures/                     # Real HTML fixtures from avtor24.ru
│   └── test_*.py                     # 9 test modules
│
├── alembic/                          # Database migrations
├── Dockerfile                        # Docker image
├── docker-compose.yml                # Docker Compose + PostgreSQL
├── railway.toml                      # Railway config
├── requirements.txt                  # Python dependencies
└── package.json                      # Node.js dependencies (docx-js)
```

---

## Database

8 tables managed via Alembic migrations:

| Table | Purpose |
|-------|---------|
| `orders` | Orders (30+ fields: data, bid, status, finances, AI metrics) |
| `messages` | Chat history with clients |
| `action_logs` | Bot action log |
| `daily_stats` | Aggregated daily statistics |
| `notifications` | Dashboard notifications (JSONB body) |
| `bot_settings` | Persistent settings (editable via UI) |
| `api_usage` | Detailed OpenAI token tracking (by model and purpose) |

---

## License

Private project. All rights reserved.
