FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ app/

# Expose port (default to 8000)
EXPOSE 8000

# Start server using shell form to allow variable expansion of $PORT
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
