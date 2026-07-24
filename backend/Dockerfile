FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Install system dependencies (required for compiling Python dependencies like PyAudio/PortAudio)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    ffmpeg \
    portaudio19-dev \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries and system dependencies for Teams Observer bot
RUN playwright install --with-deps chromium

# Copy backend application packages
COPY backend/__init__.py /app/backend/__init__.py
COPY backend/app /app/backend/app
COPY backend/copilot /app/backend/copilot

# Expose FastAPI server port
EXPOSE 8000

# Start FastAPI server using Uvicorn
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
