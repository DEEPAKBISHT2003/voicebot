FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PULSE_SERVER=unix:/tmp/pulse/native \
    XDG_RUNTIME_DIR=/tmp/pulse

WORKDIR /app

# Install system dependencies for audio processing + Chromium + PulseAudio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    portaudio19-dev \
    ffmpeg \
    libasound2-dev \
    wget \
    gnupg \
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    libasound2-plugins \
    libpulse-dev \
    libpulse0 \
    xvfb \
    x11-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser dependencies manually (Debian Trixie compatible)
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

# Install ONLY Chromium browser binary (without system deps — already installed above)
RUN playwright install chromium

# PulseAudio configuration for headless Docker
COPY pulse-daemon.conf /etc/pulse/daemon.conf
COPY pulse-default.pa /etc/pulse/default.pa
COPY asound.conf /etc/asound.conf

# Create runtime directory for PulseAudio socket
RUN mkdir -p /tmp/pulse && chmod 777 /tmp/pulse

# Create parent package init for Python import resolution
RUN mkdir -p /app/services && touch /app/services/__init__.py

# Copy service source code
COPY . /app/services/copilot

# Copy and set up entrypoint — convert line endings to LF (fixes Windows CRLF issue)
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r//' /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
