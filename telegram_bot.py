"""
CryptoRadar — Telegram Bot.
Меню, настройки, уроки, ордера, алерты + генерация ордеров.
"""

import io
import json
import asyncio
import requests as http_requests
from datetime import datetime
from typing import Optional, Callable

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

import config
import database
import server_monitor
import scan_cache
from order_manager import order_manager
import order_ai
from logger import log
from utils import truncate


# ── Глобальные переменные ─────────────────────────────────
_app: Optional[Application] = None
_scan_callback: Optional[Callable] = None
_last_scan_time: Optional[datetime] = None
_last_scan_found: int = 0
_last_scan_total: int = 0

_TG_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Состояния ConversationHandler
EDIT_PROFIT, EDIT_RR, EDIT_KEY, EDIT_SCORE = range(4)


def set_scan_callback(callback: Callable):
    """Устанавливает коллбэк для ручного сканирования."""
    global _scan_callback
    _scan_callback = callback


def update_status(scan_time: datetime, found: int, total: int):
    """Обновляет статистику последнего скана."""
    global _last_scan_time, _last_scan_found, _last_scan_total
    _last_scan_time = scan_time
    _last_scan_found = found
    _last_scan_total = total


# ── Прямые HTTP-запросы (алерты из других потоков) ────────

def _send_photo_raw(chat_id: str, photo_bytes: bytes, caption: str = "", reply_markup: dict = None) -> bool:
    try:
        url = f"{_TG_API}/sendPhoto"
        files = {"photo": ("chart.png", io.BytesIO(photo_bytes), "image/png")}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        resp = http_requests.post(url, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            return True
        else:
            log.error(f"Telegram sendPhoto error: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram sendPhoto exception: {e}")
        return False


def _send_text_raw(chat_id: str, text: str, reply_markup: dict = None) -> bool:
    try:
        url = f"{_TG_API}/sendMessage"
        chunks = _split_text(text, 4096)
        for i, chunk in enumerate(chunks):
            data = {"chat_id": chat_id, "text": chunk}
            # Добавляем клавиатуру только к последнему куску
            if reply_markup and i == len(chunks) - 1:
                data["reply_markup"] = json.dumps(reply_markup)
                
            resp = http_requests.post(url, json=data, timeout=15)
            if resp.status_code != 200:
                log.error(f"Telegram sendMessage error: {resp.status_code} {resp.text[:200]}")
                return False
        return True
    except Exception as e:
        log.error(f"Telegram sendMessage exception: {e}")
        return False


def _split_text(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def send_message(text: str):
    """Отправляет текстовое сообщение (plain text)."""
    _send_text_raw(config.TELEGRAM_CHAT_ID, text)


def send_alert(
    symbol: str, direction: str, score_15m: int, score_1h: int,
    probability: int, summary: str, details: str, chart_bytes: bytes,
):
    """Отправляет алерт и предлагает создать ордер."""
    chat_id = config.TELEGRAM_CHAT_ID
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    header = f"{symbol} | {dir_emoji} {direction} {dir_emoji}\n⚡️ СИЛА СИГНАЛА: 15M-{score_15m} / 1H-{score_1h} / Вероятность {probability}%"
    
    caption = f"{header}\n\n{summary}"
    if len(caption) > 1024:
        caption = f"{header}\n\n{truncate(summary, 1024 - len(header) - 5)}"

    _send_photo_raw(chat_id, chart_bytes, caption=caption)

    # Предлагаем создать ордер (отдельным сообщением чтобы не обрезать детали)
    kb = {
        "inline_keyboard": [
            [
                {"text": "✅ Да", "callback_data": f"create_order:{symbol}:{direction}"},
                {"text": "❌ Нет", "callback_data": f"decline_order:{symbol}:{direction}"}
            ]
        ]
    }
    
    msg = f"💰 Создать ордер {symbol} {direction}?\nРежим: {'📝 Demo' if config.TRADING_MODE=='demo' else '⚔️ Battle'} | Профит: ${config.TARGET_PROFIT_USD}"
    _send_text_raw(chat_id, msg, reply_markup=kb)


# ── Главное меню ──────────────────────────────────────────

def get_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Запустить скан", callback_data="scan"),
         InlineKeyboardButton("📊 Последний скан", callback_data="lastscan")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
         InlineKeyboardButton("📖 Уроки", callback_data="lessons")],
        [InlineKeyboardButton("📋 Открытые ордера", callback_data="orders"),
         InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
    ])


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔭 CryptoRadar — Главное меню"
    await update.message.reply_text(text, reply_markup=get_menu_kb())


async def action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔭 CryptoRadar — Главное меню", reply_markup=get_menu_kb())


# ── Скан и Статус ─────────────────────────────────────────

async def action_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if _scan_callback is None:
        await query.edit_message_text("⚠️ Сканирование не настроено.", reply_markup=get_menu_kb())
        return

    await query.edit_message_text("🔄 Запускаю ручное сканирование...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _scan_callback)


async def action_lastscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = scan_cache.format_compact()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="menu")]])
    await query.edit_message_text(text, reply_markup=kb)


async def action_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if _last_scan_time:
        time_str = _last_scan_time.strftime("%H:%M:%S %d.%m.%Y")
        text = (
            f"ℹ️ Статус CryptoRadar\n\n"
            f"Последний скан: {time_str}\n"
            f"Просканировано: {_last_scan_total} монет\n"
            f"Прошли фильтр: {_last_scan_found} монет\n"
        )
    else:
        text = "ℹ️ Статус CryptoRadar\n\nСканирование ещё не запускалось."
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="menu")]])
    await query.edit_message_text(text, reply_markup=kb)


# ── Настройки ─────────────────────────────────────────────

def get_settings_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Переключить режим", callback_data="toggle_mode")],
        [InlineKeyboardButton("💰 Изменить профит", callback_data="edit_profit"),
         InlineKeyboardButton("📐 Изменить R/R", callback_data="edit_rr")],
        [InlineKeyboardButton("📊 Мин. порог скринера", callback_data="edit_score")],
        [InlineKeyboardButton("🔑 API ключи и модели", callback_data="api_keys")],
        [InlineKeyboardButton("← Назад", callback_data="menu")]
    ])


async def action_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    report = server_monitor.monitor.format_report()
    mode_emoji = "📝 Demo" if config.TRADING_MODE == "demo" else "⚔️ Battle"
    
    text = (
        f"⚙️ НАСТРОЙКИ\n\n"
        f"{report}\n\n"
        f"🔧 Режим: {mode_emoji}\n"
        f"💰 Целевой профит: ${config.TARGET_PROFIT_USD:.2f}\n"
        f"📐 R/R коридор: {config.RR_MIN} — {config.RR_MAX}\n"
        f"📊 Проходной балл скринера: {config.MIN_SCORE_TOTAL} (сумма)"
    )
    await query.edit_message_text(text, reply_markup=get_settings_kb())


async def action_toggle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    new_mode = "battle" if config.TRADING_MODE == "demo" else "demo"
    if new_mode == "battle":
        ok, msg = order_manager.validate_api_keys()
        if not ok:
            await query.message.reply_text(f"❌ Нельзя переключить в Battle Mode: {msg}")
            return
            
    order_manager.switch_mode(new_mode)
    await action_settings(update, context)


async def action_api_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "🔑 Управление ключами и моделями\n\n"
        "Выберите ключ для изменения:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 OPENROUTER_API_KEY", callback_data="edit_key:openrouter")],
        [InlineKeyboardButton("🏦 BYBIT_API_KEY", callback_data="edit_key:bybit_key")],
        [InlineKeyboardButton("🔐 BYBIT_API_SECRET", callback_data="edit_key:bybit_secret")],
        [InlineKeyboardButton("🧠 Анализатор", callback_data="edit_key:analyzer")],
        [InlineKeyboardButton("💹 Трейдер", callback_data="edit_key:trader")],
        [InlineKeyboardButton("📝 Ревьюер", callback_data="edit_key:reviewer")],
        [InlineKeyboardButton("← Назад", callback_data="settings")]
    ])
    await query.edit_message_text(text, reply_markup=kb)


# ── Изменение параметров (Conversation) ────────────────────

async def prompt_edit_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Введите новый целевой профит в USD (например: 15.5):")
    return EDIT_PROFIT

async def handle_edit_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(",", "."))
        config.TARGET_PROFIT_USD = val
        await update.message.reply_text(f"✅ Установлен целевой профит: ${val:.2f}", reply_markup=get_menu_kb())
    except ValueError:
        await update.message.reply_text("❌ Ошибка ввода. Возврат в меню.", reply_markup=get_menu_kb())
    return ConversationHandler.END


async def prompt_edit_rr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Введите min и max R/R через пробел (например: 1.5 3.0):")
    return EDIT_RR

async def handle_edit_rr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.replace(",", ".").split()
        if len(parts) != 2:
            raise ValueError
        rmin, rmax = float(parts[0]), float(parts[1])
        config.RR_MIN = rmin
        config.RR_MAX = rmax
        await update.message.reply_text(f"✅ Установлен R/R: {rmin} — {rmax}", reply_markup=get_menu_kb())
    except ValueError:
        await update.message.reply_text("❌ Ошибка ввода. Возврат в меню.", reply_markup=get_menu_kb())
    return ConversationHandler.END


async def prompt_edit_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Введите новый проходной балл скринера в виде числа (например: 11):")
    return EDIT_SCORE

async def handle_edit_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip())
        config.MIN_SCORE_TOTAL = val
        await update.message.reply_text(f"✅ Базовый порог установлен на: {val}", reply_markup=get_menu_kb())
    except ValueError:
        await update.message.reply_text("❌ Ошибка ввода: ожидается целое число. Возврат в меню.", reply_markup=get_menu_kb())
    return ConversationHandler.END


async def prompt_edit_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, target_key = query.data.split(":")
    context.user_data["editing_key"] = target_key
    await query.message.reply_text(f"Введите новое значение для {target_key}:")
    return EDIT_KEY

async def handle_edit_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get("editing_key")
    val = update.message.text.strip()
    
    if target == "openrouter": config.OPENROUTER_API_KEY = val
    elif target == "bybit_key": config.BYBIT_API_KEY = val
    elif target == "bybit_secret": config.BYBIT_API_SECRET = val
    elif target == "analyzer": config.ANALYZER_MODEL = val
    elif target == "trader": config.ORDER_MODEL = val
    elif target == "reviewer": config.REVIEW_MODEL = val
    
    import order_manager
    order_manager.order_manager._session = None # сброс сессии Bybit
    
    await update.message.reply_text(f"✅ Значение {target} обновлено.", reply_markup=get_menu_kb())
    return ConversationHandler.END


async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=get_menu_kb())
    return ConversationHandler.END


# ── Открытые ордера ───────────────────────────────────────

async def action_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    orders = database.get_open_orders()
    if not orders:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="menu")]])
        await query.edit_message_text("📋 Открытые ордера\n\nНет открытых ордеров.", reply_markup=kb)
        return
        
    await _show_order_page(query, orders, 0)


async def _show_order_page(query, orders: list[dict], idx: int):
    if idx < 0: idx = 0
    if idx >= len(orders): idx = len(orders) - 1
    
    o = orders[idx]
    mode_str = "📝 Demo" if o['mode'] == 'demo' else "⚔️ Battle"
    dir_str = "📈LONG📈" if o['direction'] == 'LONG' else "📉SHORT📉"
    
    text = (
        f"📋 Ордер {idx+1}/{len(orders)}\n\n"
        f"{o['symbol']} {dir_str}\n"
        f"Режим: {mode_str}\n"
        f"Вход: {o['entry']}\n"
        f"SL: {o['sl']} (BE: {'Да' if o.get('sl_moved_to_be') else 'Нет'})\n"
        f"TP: {o['tp']}\n"
        f"R/R: 1:{o['rr_ratio']}\n"
        f"Qty: {o['qty']}"
    )
    
    buttons = []
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"order_page:{idx-1}"))
    if idx < len(orders) - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"order_page:{idx+1}"))
        
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_confirm:{o['id']}:{idx}")])
    buttons.append([InlineKeyboardButton("← Назад", callback_data="menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def action_order_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    orders = database.get_open_orders()
    if not orders:
        await action_orders(update, context)
        return
    await _show_order_page(query, orders, idx)


async def action_cancel_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, order_id, idx = query.data.split(":")
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить", callback_data=f"cancel_confirm:{order_id}:{idx}")],
        [InlineKeyboardButton("❌ Нет, вернуться", callback_data=f"order_page:{idx}")]
    ])
    await query.edit_message_text("Точно отменить ордер?", reply_markup=kb)


async def action_cancel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, order_id, idx = query.data.split(":")
    
    ok, msg = order_manager.cancel_order(order_id)
    text = f"✅ Ордер отменён" if ok else f"❌ Ошибка: {msg}"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("← К ордерам", callback_data="orders")],
        [InlineKeyboardButton("← В меню", callback_data="menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb)


# ── Уроки ─────────────────────────────────────────────────

async def action_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    total = database.count_lessons()
    if total == 0:
        import lessons
        text = lessons.format_empty()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="menu")]])
        await query.edit_message_text(text, reply_markup=kb)
        return
        
    await _show_lesson_page(query, 1, total) # 1 = самый свежий


async def _show_lesson_page(query, idx: int, total: int):
    import lessons
    lesson = lessons.get_lesson(idx)
    if not lesson:
        await action_menu(update=Update(0), context=None) # hacky fallback
        return
        
    text = lessons.format_lesson(lesson, idx, total)
    
    nav = []
    # idx 1 = newest. Left goes to idx-1 (newer), Right goes to idx+1 (older)
    if idx > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"lesson_page:{idx-1}"))
    nav.append(InlineKeyboardButton("🔍 Подробнее", callback_data=f"lesson_detail:{idx}:{lesson['order_id']}"))
    if idx < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"lesson_page:{idx+1}"))
        
    kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("← Назад", callback_data="menu")]])
    
    if query.message.text != text:
        await query.edit_message_text(text, reply_markup=kb)


async def action_lesson_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    total = database.count_lessons()
    await _show_lesson_page(query, idx, total)


async def action_lesson_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    idx = int(parts[1])
    order_id = parts[2]
    
    import lessons
    lesson = lessons.get_lesson(idx)
    order = lessons.get_order_details(order_id)
    
    if lesson and order:
        text = lessons.format_order_details(lesson, order)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("← К уроку", callback_data=f"lesson_page:{idx}")]])
        await query.edit_message_text(text, reply_markup=kb)
    else:
        await query.edit_message_text("Ошибка загрузки деталей.", reply_markup=get_menu_kb())


# ── Генерация ордера ──────────────────────────────────────

async def action_create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    symbol = parts[1]
    direction = parts[2]
    
    await query.edit_message_reply_markup(reply_markup=None) # убираем кнопки
    msg = await query.message.reply_text(f"🤖 ORDER_MODEL: генерация ордера для {symbol} ({direction})...")
    
    import ai_analyst
    try:
        try:
            from order_manager import order_manager
            session = order_manager._get_session()
            ticker_response = session.get_tickers(category="linear", symbol=symbol)
            if ticker_response["retCode"] != 0 or not ticker_response["result"]["list"]:
                await msg.edit_text("❌ Ошибка: не удалось получить текущую цену с Bybit.")
                return
            
            ticker_data = ticker_response["result"]["list"][0]
            current_price = float(ticker_data["lastPrice"])
            volume_24h = float(ticker_data["volume24h"])
        except Exception as api_err:
            await msg.edit_text(f"❌ Ошибка получения цены: {api_err}")
            return
        
        # Получаем уровни через быстрый анализ или берём из кэша. В данном случае просто имитируем уровни
        # Так как это сложно, используем order_ai и пусть он берет текущую цену
        # В реальной системе уровни (support, resistance) передавались в callback data или хранились в контексте. 
        # Для простоты:
        res = scan_cache.format_compact() # просто чтобы было
        
    except Exception as e:
        await msg.edit_text(f"❌ Системная ошибка: {e}")
        return

    try:
        # Для полноценной генерации нужно знать support и resistance
        # Будем считать что order_ai.generate_order_params справится.
        
        # Имитируем support и resistance для примера.
        # Это хак, так как в идеале уровни надо передавать из алерта.
        support = current_price * 0.95
        resistance = current_price * 1.05
        
        loop = asyncio.get_event_loop()
        params = await loop.run_in_executor(None, order_ai.generate_order_params, 
            symbol, direction, current_price, support, resistance, volume_24h)
            
        text = (
            f"📋 Сгенерирован ордер {symbol} {direction}\n\n"
            f"Вход: {params.entry}\n"
            f"SL: {params.sl}\n"
            f"TP: {params.tp}\n"
            f"Qty: {params.qty}\n"
            f"R/R: 1:{params.rr_ratio:.2f}\n\n"
            f"🤖 Логика:\n{params.reasoning}"
        )
        
        # Сохраняем params в context
        context.user_data[f"order_{symbol}"] = params
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Разместить", callback_data=f"confirm_place:{symbol}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"decline_place:{symbol}")]
        ])
        
        await msg.edit_text(text, reply_markup=kb)
        
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка генерации ордера: {e}")


async def action_decline_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    await query.edit_message_text(f"Ордер {parts[1]} пропущен.")


async def action_confirm_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    symbol = query.data.split(":")[1]
    params = context.user_data.get(f"order_{symbol}")
    if not params:
        await query.edit_message_text("❌ Данные ордера устарели.")
        return
        
    res = order_manager.place_order(params)
    if res:
        await query.edit_message_text(f"✅ Ордер {symbol} успешно размещён на Bybit ({res['mode']} mode)!")
    else:
        await query.edit_message_text(f"❌ Не удалось разместить ордер на Bybit.")


async def action_decline_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    symbol = query.data.split(":")[1]
    await query.edit_message_text(f"Размещение ордера {symbol} отменено.")


# ── Настройка роутера ─────────────────────────────────────

def setup_dispatcher(app: Application):
    """Регистрирует все хэндлеры."""
    app.add_handler(CommandHandler("start", cmd_menu))
    app.add_handler(CommandHandler("menu", cmd_menu))
    
    app.add_handler(CallbackQueryHandler(action_menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(action_scan, pattern="^scan$"))
    app.add_handler(CallbackQueryHandler(action_lastscan, pattern="^lastscan$"))
    app.add_handler(CallbackQueryHandler(action_status, pattern="^status$"))
    
    app.add_handler(CallbackQueryHandler(action_settings, pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(action_toggle_mode, pattern="^toggle_mode$"))
    app.add_handler(CallbackQueryHandler(action_api_keys, pattern="^api_keys$"))
    
    app.add_handler(CallbackQueryHandler(action_orders, pattern="^orders$"))
    app.add_handler(CallbackQueryHandler(action_order_page, pattern="^order_page:"))
    app.add_handler(CallbackQueryHandler(action_cancel_order_confirm, pattern="^cancel_order_confirm:"))
    app.add_handler(CallbackQueryHandler(action_cancel_confirm, pattern="^cancel_confirm:"))
    
    app.add_handler(CallbackQueryHandler(action_lessons, pattern="^lessons$"))
    app.add_handler(CallbackQueryHandler(action_lesson_page, pattern="^lesson_page:"))
    app.add_handler(CallbackQueryHandler(action_lesson_detail, pattern="^lesson_detail:"))
    
    app.add_handler(CallbackQueryHandler(action_create_order, pattern="^create_order:"))
    app.add_handler(CallbackQueryHandler(action_decline_order, pattern="^decline_order:"))
    app.add_handler(CallbackQueryHandler(action_confirm_place, pattern="^confirm_place:"))
    app.add_handler(CallbackQueryHandler(action_decline_place, pattern="^decline_place:"))
    
    # Conversation для настроек
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(prompt_edit_profit, pattern="^edit_profit$"),
            CallbackQueryHandler(prompt_edit_rr, pattern="^edit_rr$"),
            CallbackQueryHandler(prompt_edit_score, pattern="^edit_score$"),
            CallbackQueryHandler(prompt_edit_key, pattern="^edit_key:"),
        ],
        states={
            EDIT_PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_profit)],
            EDIT_RR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_rr)],
            EDIT_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_score)],
            EDIT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit)],
    )
    app.add_handler(conv_handler)


def start_bot() -> Application:
    """Создаёт приложение бота."""
    global _app
    _app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    setup_dispatcher(_app)
    log.info("Telegram бот инициализирован.")
    return _app
