FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required for PyAudio C compilation & audio support
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    portaudio19-dev \
    ffmpeg \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Create parent package init for Python import resolution
RUN mkdir -p /app/services && touch /app/services/__init__.py

# Copy service source code
COPY . /app/services/copilot

EXPOSE 8001

CMD ["uvicorn", "services.copilot.src.main:app", "--host", "0.0.0.0", "--port", "8001"]