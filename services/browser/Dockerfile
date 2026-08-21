FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies for audio processing + Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    portaudio19-dev \
    ffmpeg \
    libasound2-dev \
    wget \
    gnupg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser dependencies (Debian Trixie compatible)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2t64 \
    libatspi2.0-0 \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-unifont \
    && rm -rf /var/lib/apt/lists/*

# Install ONLY Chromium browser binary
RUN playwright install chromium

# Create parent package init for Python import resolution
RUN mkdir -p /app/services && touch /app/services/__init__.py

COPY . /app/services/browser

EXPOSE 8002

CMD ["sh", "-c", "uvicorn services.browser.src.main:app --host 0.0.0.0 --port ${BROWSER_PORT:-8002}"]
