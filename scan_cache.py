"""
CryptoRadar — Кэш последнего скана.
Сохраняет результаты в SQLite, форматирует компактный вывод.
"""

from datetime import datetime
from typing import Optional

import database
from models import ScreenResult


def save_scan(results: list[ScreenResult], total_scanned: int):
    """Сохраняет результаты скана в SQLite."""
    data = []
    for r in results:
        data.append({
            "symbol": r.symbol,
            "direction": r.direction.value,
            "score_15m": r.score_15m,
            "score_1h": r.score_1h,
            "last_price": r.last_price,
        })
    database.save_scan_results(data, total_scanned)


def format_compact() -> str:
    """
    Компактный формат для /lastscan:
    
    📊 Последний скан (14:00 21.04):
    
    1. BTCUSDT — 7/10 🟢 LONG
    2. ETHUSDT — 6/10 🔴 SHORT
    ...
    Просканировано: 30 монет
    """
    results, scanned_at, total = database.get_last_scan()

    if not results:
        return "📊 Последний скан\n\nДанных нет — скан ещё не запускался."

    # Форматируем дату
    try:
        dt = datetime.fromisoformat(scanned_at)
        time_str = dt.strftime("%H:%M %d.%m")
    except Exception:
        time_str = scanned_at

    lines = [f"📊 Последний скан ({time_str}):\n"]

    for i, r in enumerate(results, 1):
        min_score = min(r["score_15m"], r["score_1h"])
        direction = r["direction"]
        emoji = "🟢" if direction == "LONG" else "🔴"
        lines.append(f"{i}. {r['symbol']} — {min_score}/10 {emoji} {direction}")

    lines.append(f"\nПросканировано: {total} монет")
    return "\n".join(lines)
