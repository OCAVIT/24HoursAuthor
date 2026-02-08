FROM python:3.12-slim

WORKDIR /app

# Node.js для docx-js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs chromium fonts-liberation libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdrm2 libgbm1 libgtk-3-0 libnss3 libxcomposite1 \
    libxdamage1 libxrandr2 xdg-utils && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/browsers
RUN pip install playwright && playwright install chromium

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Зависимости Node.js
COPY package.json .
RUN npm install

COPY . .

# Graceful shutdown: SIGTERM → uvicorn обработает корректно
STOPSIGNAL SIGTERM

# Production: миграции + запуск (без тестов)
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
