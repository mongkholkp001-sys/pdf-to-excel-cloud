# PDF to Excel System - Cloud Edition (Google Cloud Run & Docker Ready)
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV TESSDATA_PREFIX=/app/tessdata

# Install system dependencies & Tesseract OCR with Thai and English language packs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-tha \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure storage and tessdata directories exist
RUN mkdir -p /app/uploads /app/outputs /app/data /app/tessdata

# Expose standard Cloud Run port
EXPOSE 8080

# Run with Gunicorn Production WSGI server
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 8 --timeout 300 app:app
