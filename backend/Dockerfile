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
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application package
COPY backend/app /app/backend/app

# Expose FastAPI server port
EXPOSE 8000

# Start FastAPI server using Uvicorn
CMD uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
