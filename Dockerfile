FROM python:3.11-slim

# Системные зависимости для matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости (кэшируются отдельным слоем)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

# Директории для логов и SQLite БД
RUN mkdir -p /app/logs /app/data

CMD ["python", "main.py"]
