"""
CryptoRadar — Логирование.
Ротация файлов + кастомный TelegramHandler для ошибок.
"""

import logging
import os
import time
from logging.handlers import RotatingFileHandler

import config


class TelegramHandler(logging.Handler):
    """
    Кастомный хендлер: отправляет ERROR/CRITICAL логи в Telegram.
    Throttling: не чаще одного сообщения в TG_ERROR_THROTTLE_SEC секунд.
    """

    def __init__(self, bot_token: str, chat_id: str, throttle_sec: int = 60):
        super().__init__(level=logging.ERROR)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.throttle_sec = throttle_sec
        self._last_sent = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        now = time.time()
        if now - self._last_sent < self.throttle_sec:
            return

        try:
            import requests
            text = self.format(record)
            # Обрезаем до лимита Telegram (4096 символов)
            if len(text) > 4000:
                text = text[:4000] + "\n... (обрезано)"

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": f"⚠️ CryptoRadar Error:\n\n{text}",
                "parse_mode": None,  # plain text — чтобы не ломалось на спецсимволах
            }
            requests.post(url, json=payload, timeout=10)
            self._last_sent = now
        except Exception:
            # Не ломаем приложение если Telegram недоступен
            self.handleError(record)


def setup_logger(name: str = "crypto_radar") -> logging.Logger:
    """
    Настраивает и возвращает логгер:
    - stdout (для Docker)
    - файл с ротацией
    - Telegram хендлер для ошибок
    """
    logger = logging.getLogger(name)

    # Избегаем дублирования хендлеров при повторном вызове
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Формат
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── stdout ──
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # ── Файл с ротацией ──
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.LOG_DIR, config.LOG_FILE)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # ── Telegram хендлер ──
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        tg_handler = TelegramHandler(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
            throttle_sec=config.TG_ERROR_THROTTLE_SEC,
        )
        tg_handler.setFormatter(fmt)
        logger.addHandler(tg_handler)

    return logger


# Глобальный логгер
log = setup_logger()
