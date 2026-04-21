#!/bin/bash
# ── CryptoRadar — Скрипт обновления на сервере ──
# Использование: ./deploy.sh
# Выполняет: git pull → docker rebuild → restart
# ─────────────────────────────────────────────────

set -e

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$APP_DIR"

echo "🔄 CryptoRadar: обновление..."

# 1. Забираем обновления из git
echo "📥 Git pull..."
git pull origin main

# 2. Пересобираем и перезапускаем контейнер
echo "🐳 Docker rebuild + restart..."
docker-compose down
docker-compose up -d --build

# 3. Проверяем что контейнер запустился
sleep 3
if docker-compose ps | grep -q "Up"; then
    echo "✅ CryptoRadar обновлён и запущен!"
    docker-compose logs --tail=10
else
    echo "❌ Ошибка! Контейнер не запустился."
    docker-compose logs --tail=30
    exit 1
fi
