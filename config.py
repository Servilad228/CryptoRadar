"""
CryptoRadar — Конфигурация.
Загружает настройки из .env, определяет все пороги и константы.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ── Сканирование ─────────────────────────────────────────
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL", "60"))
SCAN_AT_MINUTE = 0  # запуск строго в :00 — свечи закрыты
TOP_N_COINS = int(os.getenv("TOP_N_COINS", "30"))
CATEGORY = "linear"  # USDT Perpetuals — максимальные объёмы
TIMEFRAMES = ["15", "60"]  # 15m и 1h (формат Bybit API v5)
KLINE_LIMIT = 200  # количество свечей для загрузки

# ── Скоринг ───────────────────────────────────────────────
MIN_SCORE_15M = int(os.getenv("MIN_SCORE_15M", "4"))  # проходной балл для 15 мин
MIN_SCORE_1H = int(os.getenv("MIN_SCORE_1H", "4"))    # проходной балл для 1 часа

# ── Индикаторы: пороги ────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

EMA_FAST = 12
EMA_SLOW = 26
EMA_TREND = 200

BB_PERIOD = 20
BB_STD = 2.0

VOLUME_SPIKE_MULTIPLIER = 2.0
VOLUME_MA_PERIOD = 20

OBV_TREND_BARS = 5

STOCH_RSI_PERIOD = 14
STOCH_RSI_OVERSOLD = 20
STOCH_RSI_OVERBOUGHT = 80

ADX_PERIOD = 14
ADX_THRESHOLD = 25

# ── AI Models (3 роли) ────────────────────────────────────
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ANALYZER_MODEL = os.getenv("ANALYZER_MODEL", "deepseek/deepseek-chat-v3-0324")
ORDER_MODEL = os.getenv("ORDER_MODEL", "anthropic/claude-3.5-sonnet")
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "anthropic/claude-3.5-sonnet")
AI_TIMEOUT = 30  # секунд
AI_MAX_RETRIES = 2

# ── AI Tips (петля обучения) ─────────────────────────────
MAX_TIPS_IN_PROMPT = int(os.getenv("MAX_TIPS_IN_PROMPT", "10"))

# ── Trading ──────────────────────────────────────────────
TRADING_MODE = os.getenv("TRADING_MODE", "demo")  # demo | battle
TARGET_PROFIT_USD = float(os.getenv("TARGET_PROFIT_USD", "15.0"))
RR_MIN = float(os.getenv("RR_MIN", "1.5"))
RR_MAX = float(os.getenv("RR_MAX", "3.0"))
BREAKEVEN_TRIGGER_PCT = float(os.getenv("BREAKEVEN_TRIGGER_PCT", "40.0"))

# ── Position Tracking ────────────────────────────────────
DEMO_CHECK_INTERVAL_MIN = int(os.getenv("DEMO_CHECK_INTERVAL_MIN", "10"))

# ── Database ─────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "data/cryptoradar.db")

# ── Логирование ──────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE = "crypto_radar.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3
TG_ERROR_THROTTLE_SEC = 60  # не чаще 1 ошибки/мин в Telegram

# ── Self-test ────────────────────────────────────────────
SELFTEST_HOUR = 6  # ежедневный self-test в 06:00
SELFTEST_MINUTE = 0

# ── Rate limiting ────────────────────────────────────────
API_REQUEST_DELAY = 0.1  # 100ms между запросами к Bybit

