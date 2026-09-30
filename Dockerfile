FROM python:3.11-slim

# Sistem bagimliliklari
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python paket bagimliliklari
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodlarini kopyala
COPY . .

# Kalici veri klasorunu hazirla
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    HOST=0.0.0.0 \
    PORT=8000 \
    BASE_PATH=/graph \
    IG_ARTIFACT_ROOT=/app/data

EXPOSE 8000

CMD ["python", "-m", "backend", "--host", "0.0.0.0", "--port", "8000", "--artifacts", "/app/data"]
