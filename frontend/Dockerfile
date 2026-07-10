FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Install system dependencies (compilation tools for pyaudio/portaudio if requested)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application package (required for parsing resumes)
COPY backend/app /app/backend/app

# Copy frontend application files and style subdirectories
COPY frontend /app/frontend

# Expose Streamlit port
EXPOSE 8502

# Start Streamlit application
CMD ["streamlit", "run", "frontend/app.py", "--server.port", "8502", "--server.address", "0.0.0.0"]
