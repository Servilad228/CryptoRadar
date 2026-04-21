"""
CryptoRadar — Telegram Bot.
Отправка алертов, команды /start /scan /status.
Осторожная работа с MarkdownV2.
"""

import io
import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

import config
from logger import log
from utils import escape_md, truncate


# ── Глобальные переменные ─────────────────────────────────
_app: Optional[Application] = None
_scan_callback: Optional[Callable[[], Awaitable[None]]] = None
_last_scan_time: Optional[datetime] = None
_last_scan_found: int = 0
_last_scan_total: int = 0


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


# ── Команды ────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start."""
    text = (
        "🔭 *CryptoRadar* — автономный мониторинг криптовалют\n\n"
        "Сканирую топ\\-30 монет по объёмам на Bybit каждый час\\.\n"
        "Анализирую 10 технических индикаторов на 15m и 1h\\.\n"
        "Прошедшие монеты отправляю с AI\\-резюме и графиком\\.\n\n"
        "📋 *Команды:*\n"
        "/scan — ручной запуск сканирования\n"
        "/status — текущий статус бота"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /status."""
    if _last_scan_time:
        time_str = escape_md(_last_scan_time.strftime("%H:%M:%S %d.%m.%Y"))
        text = (
            f"📊 *Статус CryptoRadar*\n\n"
            f"Последний скан: {time_str}\n"
            f"Просканировано: {escape_md(str(_last_scan_total))} монет\n"
            f"Прошли фильтр: {escape_md(str(_last_scan_found))} монет\n"
            f"Следующий скан: в :00"
        )
    else:
        text = "📊 *Статус CryptoRadar*\n\nСканирование ещё не запускалось\\."

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def _cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /scan — ручной запуск сканирования."""
    if _scan_callback is None:
        await update.message.reply_text("⚠️ Сканирование не настроено\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    await update.message.reply_text("🔄 Запускаю ручное сканирование\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

    try:
        # Запускаем скан в фоне чтобы не блокировать бота
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _scan_callback)
    except Exception as e:
        error_text = f"❌ Ошибка сканирования: {escape_md(str(e))}"
        await update.message.reply_text(error_text, parse_mode=ParseMode.MARKDOWN_V2)


# ── Отправка алертов ───────────────────────────────────────

async def _send_alert_async(
    bot: Bot,
    symbol: str,
    direction: str,
    score_15m: int,
    score_1h: int,
    summary: str,
    details: str,
    chart_bytes: Optional[bytes],
):
    """Отправляет алерт с графиком и анализом (async)."""
    chat_id = config.TELEGRAM_CHAT_ID

    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    # ── Caption для фото (до 1024 символов) ──
    header = (
        f"{dir_emoji} {escape_md(symbol)} | {escape_md(direction)} | "
        f"Score: 15m\\={escape_md(str(score_15m))} / 1h\\={escape_md(str(score_1h))}\n\n"
    )

    # Резюме — экранируем, но сохраняем читаемость
    safe_summary = escape_md(summary)
    caption = header + truncate(safe_summary, 1024 - len(header) - 10)

    try:
        if chart_bytes:
            photo = io.BytesIO(chart_bytes)
            photo.name = f"{symbol}_chart.png"
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
            )

        # ── Полный анализ отдельным сообщением ──
        if details and len(details) > len(summary) + 50:
            # Отправляем plain text чтобы не ломалось на спецсимволах AI-ответа
            detail_text = f"📋 Полный анализ {symbol}:\n\n{details}"
            # Telegram лимит 4096 символов
            if len(detail_text) > 4000:
                detail_text = detail_text[:4000] + "\n... (обрезано)"
            await bot.send_message(
                chat_id=chat_id,
                text=detail_text,
            )

        log.info(f"Telegram алерт отправлен: {symbol}")

    except Exception as e:
        log.error(f"Ошибка отправки алерта {symbol} в Telegram: {e}")
        # Попытка отправить plain text как fallback
        try:
            fallback = f"{dir_emoji} {symbol} | {direction} | Score: 15m={score_15m} / 1h={score_1h}\n\n{summary}"
            await bot.send_message(chat_id=chat_id, text=fallback[:4000])
        except Exception as e2:
            log.error(f"Fallback отправки тоже провалился: {e2}")


def send_alert(
    symbol: str,
    direction: str,
    score_15m: int,
    score_1h: int,
    summary: str,
    details: str,
    chart_bytes: Optional[bytes] = None,
):
    """Синхронная обёртка для отправки алерта."""
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _send_alert_async(bot, symbol, direction, score_15m, score_1h,
                              summary, details, chart_bytes)
        )
    finally:
        loop.close()


async def _send_message_async(bot: Bot, text: str, parse_mode=None):
    """Отправляет текстовое сообщение."""
    try:
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=parse_mode,
        )
    except Exception as e:
        log.error(f"Ошибка отправки сообщения в Telegram: {e}")


def send_message(text: str, parse_mode=None):
    """Синхронная обёртка для отправки текстового сообщения."""
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_send_message_async(bot, text, parse_mode))
    finally:
        loop.close()


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


# ── Запуск бота (polling) ──────────────────────────────────

def start_bot() -> Application:
    """
    Создаёт и настраивает Telegram-бота.
    Возвращает Application для запуска polling.
    """
    global _app
    _app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    _app.add_handler(CommandHandler("start", _cmd_start))
    _app.add_handler(CommandHandler("status", _cmd_status))
    _app.add_handler(CommandHandler("scan", _cmd_scan))

    log.info("Telegram бот настроен (команды: /start, /scan, /status)")
    return _app
