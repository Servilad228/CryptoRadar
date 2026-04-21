"""
CryptoRadar — Отображение уроков.
Форматирование + пагинация (1 = самый свежий).
"""

from typing import Optional

import database
from utils import format_price


def get_total() -> int:
    """Количество уроков."""
    return database.count_lessons()


def get_lesson(index: int) -> Optional[dict]:
    """
    Получает урок по индексу (1 = самый свежий).
    Возвращает None если не найден.
    """
    return database.get_lesson_by_index(index)


def get_order_details(order_id: str) -> Optional[dict]:
    """Получает детали ордера для кнопки 'Подробнее'."""
    return database.get_lesson_order_details(order_id)


def format_lesson(lesson: dict, index: int, total: int) -> str:
    """
    Форматирует урок для отображения в Telegram.
    
    📖 Урок 1/12
    ETHUSDT 📉SHORT📉 ❌ SL Hit
    Режим: 📝 Demo
    💸 PnL: -$8.50 | R/R: 1:2.1
    📉 Вход: 3,420.00 → SL: 3,490.00
    
    🔍 Разбор:
    ...
    """
    symbol = lesson["symbol"]
    direction = lesson["direction"]
    close_reason = lesson["close_reason"]
    pnl = lesson["pnl"]
    mode = lesson.get("mode", "demo")

    # Эмодзи направления
    if direction == "LONG":
        dir_str = "📈LONG📈"
    else:
        dir_str = "📉SHORT📉"

    # Эмодзи результата
    if close_reason == "tp_hit":
        result_str = "✅ TP Hit"
    elif close_reason == "sl_hit":
        result_str = "❌ SL Hit"
    else:
        result_str = f"📋 {close_reason}"

    # Режим
    mode_str = "📝 Demo" if mode == "demo" else "⚔️ Battle"

    # PnL
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_str = f"{pnl_sign}${pnl:.2f}"

    # Формируем текст
    lines = [
        f"📖 Урок {index}/{total}",
        "",
        f"{symbol} {dir_str} {result_str}",
        f"Режим: {mode_str}",
        f"💸 PnL: {pnl_str}",
        f"Вход: {format_price(lesson['entry'])} → ",
    ]

    # Показываем куда закрылось
    if close_reason == "sl_hit":
        lines[-1] += f"SL: {format_price(lesson['sl'])}"
    else:
        lines[-1] += f"TP: {format_price(lesson['tp'])}"

    # Анализ
    lines.append("")
    lines.append(lesson.get("analysis", "Анализ недоступен"))

    return "\n".join(lines)


def format_order_details(lesson: dict, order: dict) -> str:
    """
    Форматирует детали ордера (кнопка 'Подробнее').
    
    📋 Детали ордера — ETHUSDT SHORT
    ...
    """
    symbol = lesson["symbol"]
    direction = lesson["direction"]
    mode = order.get("mode", lesson.get("mode", "demo"))

    mode_str = "📝 Demo" if mode == "demo" else "⚔️ Battle"

    lines = [
        f"📋 Детали ордера — {symbol} {direction}",
        "",
        f"Режим: {mode_str}",
        f"Создан: {order.get('created_at', 'N/A')}",
        "",
        "📊 Параметры:",
        f"  Вход: {format_price(order.get('entry', lesson['entry']))}",
        f"  SL: {format_price(order.get('sl', lesson['sl']))}",
        f"  TP: {format_price(order.get('tp', lesson['tp']))}",
        f"  R/R: 1:{order.get('rr_ratio', 'N/A')}",
        f"  Qty: {order.get('qty', 'N/A')}",
        "",
        "📈 Результат:",
        f"  Закрыт: {order.get('closed_at', 'N/A')}",
        f"  Цена: {format_price(order.get('close_price', 0))} ({lesson['close_reason']})",
        f"  PnL: ${order.get('pnl', lesson['pnl']):.2f}",
    ]

    # AI reasoning
    ai_reasoning = order.get("ai_reasoning", "")
    if not ai_reasoning:
        # Пробуем найти в open_orders (если ордер был туда записан с reasoning)
        ai_reasoning = order.get("ai_reasoning", "Нет данных")

    lines.extend([
        "",
        "🤖 Логика AI при создании:",
        ai_reasoning,
    ])

    return "\n".join(lines)


def format_empty() -> str:
    """Сообщение когда уроков нет."""
    return "📖 Уроки\n\nУроков пока нет 📭\nОни появятся после закрытия первого ордера."
