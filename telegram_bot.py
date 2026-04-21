"""
CryptoRadar — Telegram Bot.
Отправка алертов через прямые HTTP-запросы к Telegram API.
Приём команд через python-telegram-bot polling.
"""

import io
import asyncio
import requests as http_requests
from datetime import datetime
from typing import Optional, Callable

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import config
from logger import log
from utils import truncate


# ── Глобальные переменные ─────────────────────────────────
_app: Optional[Application] = None
_scan_callback: Optional[Callable] = None
_last_scan_time: Optional[datetime] = None
_last_scan_found: int = 0
_last_scan_total: int = 0

_TG_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def set_scan_callback(callback: Callable):
    """Устанавливает коллбэк для ручного сканирования из /scan."""
    global _scan_callback
    _scan_callback = callback


def update_status(scan_time: datetime, found: int, total: int):
    """Обновляет статистику последнего скана."""
    global _last_scan_time, _last_scan_found, _last_scan_total
    _last_scan_time = scan_time
    _last_scan_found = found
    _last_scan_total = total


# ── Прямые HTTP-запросы к Telegram API ─────────────────────

def _send_photo_raw(chat_id: str, photo_bytes: bytes, caption: str = "") -> bool:
    """Отправляет фото через прямой HTTP POST."""
    try:
        url = f"{_TG_API}/sendPhoto"
        files = {"photo": ("chart.png", io.BytesIO(photo_bytes), "image/png")}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]  # Telegram лимит caption
        
        resp = http_requests.post(url, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            return True
        else:
            log.error(f"Telegram sendPhoto error: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram sendPhoto exception: {e}")
        return False


def _send_text_raw(chat_id: str, text: str) -> bool:
    """Отправляет текстовое сообщение через прямой HTTP POST (plain text)."""
    try:
        url = f"{_TG_API}/sendMessage"
        # Нарезаем на куски по 4096 символов (лимит Telegram)
        chunks = _split_text(text, 4096)
        for chunk in chunks:
            data = {"chat_id": chat_id, "text": chunk}
            resp = http_requests.post(url, json=data, timeout=15)
            if resp.status_code != 200:
                log.error(f"Telegram sendMessage error: {resp.status_code} {resp.text[:200]}")
                return False
        return True
    except Exception as e:
        log.error(f"Telegram sendMessage exception: {e}")
        return False


def _split_text(text: str, max_len: int = 4096) -> list[str]:
    """Нарезает текст на куски, стараясь резать по переносам строк."""
    if len(text) <= max_len:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        
        # Ищем последний перенос строки до лимита
        cut = text.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            cut = max_len
        
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    
    return chunks


# ── Отправка алертов ───────────────────────────────────────

def send_alert(
    symbol: str,
    direction: str,
    score_15m: int,
    score_1h: int,
    summary: str,
    details: str,
    chart_bytes: Optional[bytes] = None,
):
    """Отправляет алерт: график + краткий caption + полный анализ."""
    chat_id = config.TELEGRAM_CHAT_ID
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    # Заголовок
    header = f"{dir_emoji} {symbol} | {direction} | Score: 15m={score_15m} / 1h={score_1h}"

    # 1. Отправляем график с коротким caption
    if chart_bytes:
        caption = f"{header}\n\n{summary}"
        if len(caption) > 1024:
            # Caption до 1024 символов — обрезаем summary
            caption = f"{header}\n\n{truncate(summary, 1024 - len(header) - 5)}"
        
        ok = _send_photo_raw(chat_id, chart_bytes, caption)
        if not ok:
            log.warning(f"Фото не отправлено для {symbol}, отправляю текстом")
            _send_text_raw(chat_id, f"{header}\n\n{summary}")
    else:
        _send_text_raw(chat_id, f"{header}\n\n{summary}")

    # 2. Полный анализ отдельным сообщением (plain text, без Markdown)
    if details:
        full_text = f"📋 Полный анализ {symbol}:\n\n{details}"
        _send_text_raw(chat_id, full_text)

    log.info(f"Telegram алерт отправлен: {symbol}")


def send_message(text: str, parse_mode=None):
    """Отправляет текстовое сообщение."""
    _send_text_raw(config.TELEGRAM_CHAT_ID, text)


def send_status_report(total: int, passed: int, passed_symbols: list[str]):
    """Отправляет итоговый отчёт после скана."""
    now = datetime.now().strftime("%H:%M %d.%m.%Y")
    symbols_str = ", ".join(passed_symbols) if passed_symbols else "нет"
    text = (
        f"📊 Отчёт скана ({now})\n\n"
        f"Просканировано: {total} монет\n"
        f"Прошли фильтр: {passed}\n"
        f"Монеты: {symbols_str}"
    )
    send_message(text)


def send_selftest_report(report: str):
    """Отправляет отчёт ежедневного self-test."""
    send_message(f"🔧 Daily Self-Test Report\n\n{report}")


# ── Команды бота (polling) ─────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start."""
    text = (
        "🔭 CryptoRadar — автономный мониторинг криптовалют\n\n"
        "Сканирую топ-30 монет по объёмам на Bybit каждый час.\n"
        "Анализирую 10 технических индикаторов на 15m и 1h.\n"
        "Прошедшие монеты отправляю с AI-резюме и графиком.\n\n"
        "📋 Команды:\n"
        "/scan — ручной запуск сканирования\n"
        "/status — текущий статус бота"
    )
    await update.message.reply_text(text)


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /status."""
    if _last_scan_time:
        time_str = _last_scan_time.strftime("%H:%M:%S %d.%m.%Y")
        text = (
            f"📊 Статус CryptoRadar\n\n"
            f"Последний скан: {time_str}\n"
            f"Просканировано: {_last_scan_total} монет\n"
            f"Прошли фильтр: {_last_scan_found} монет\n"
            f"Следующий скан: в :00"
        )
    else:
        text = "📊 Статус CryptoRadar\n\nСканирование ещё не запускалось."

    await update.message.reply_text(text)


async def _cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /scan — ручной запуск сканирования."""
    if _scan_callback is None:
        await update.message.reply_text("⚠️ Сканирование не настроено.")
        return

    await update.message.reply_text("🔄 Запускаю ручное сканирование...")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _scan_callback)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка сканирования: {e}")


# ── Запуск бота ────────────────────────────────────────────

def start_bot() -> Application:
    """Создаёт и настраивает Telegram-бота для polling."""
    global _app
    _app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    _app.add_handler(CommandHandler("start", _cmd_start))
    _app.add_handler(CommandHandler("status", _cmd_status))
    _app.add_handler(CommandHandler("scan", _cmd_scan))

    log.info("Telegram бот настроен (команды: /start, /scan, /status)")
    return _app
