FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required for Pipecat audio processing and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    portaudio19-dev \
    python3-pyaudio \
    ffmpeg \
    libasound2-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create parent package init for Python import resolution
RUN mkdir -p /app/services && touch /app/services/__init__.py

# Copy service source code
COPY . /app/services/interview

EXPOSE 8000

CMD ["uvicorn", "services.interview.src.main:app", "--host", "0.0.0.0", "--port", "8000"]