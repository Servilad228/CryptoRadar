"""
CryptoRadar — SQLite база данных.
5 таблиц: open_orders, closed_orders, lessons, ai_tips, last_scan.
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import config
from logger import log


def _ensure_dir():
    """Создаёт директорию для БД если не существует."""
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


@contextmanager
def get_connection():
    """Thread-safe соединение с БД."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Создаёт все таблицы при первом запуске."""
    _ensure_dir()

    with get_connection() as conn:
        conn.executescript("""
            -- Открытые ордера (demo + battle)
            CREATE TABLE IF NOT EXISTS open_orders (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                qty REAL NOT NULL,
                rr_ratio REAL NOT NULL,
                mode TEXT NOT NULL,
                bybit_order_id TEXT DEFAULT '',
                sl_moved_to_be INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                ai_reasoning TEXT DEFAULT ''
            );

            -- Закрытые ордера (история)
            CREATE TABLE IF NOT EXISTS closed_orders (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                qty REAL NOT NULL,
                rr_ratio REAL NOT NULL,
                mode TEXT NOT NULL,
                bybit_order_id TEXT DEFAULT '',
                close_price REAL NOT NULL,
                close_reason TEXT NOT NULL,
                pnl REAL NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT NOT NULL
            );

            -- Уроки (из каждой сделки — TP и SL, demo и battle)
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                close_reason TEXT NOT NULL,
                pnl REAL NOT NULL,
                mode TEXT NOT NULL,
                analysis TEXT NOT NULL,
                chart_snapshot BLOB,
                created_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES closed_orders(id)
            );

            -- AI советы (петля обучения)
            CREATE TABLE IF NOT EXISTS ai_tips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_role TEXT NOT NULL,
                tip TEXT NOT NULL,
                source_order_id TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Последний скан
            CREATE TABLE IF NOT EXISTS last_scan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                score_15m INTEGER NOT NULL,
                score_1h INTEGER NOT NULL,
                last_price REAL NOT NULL,
                scanned_at TEXT NOT NULL,
                total_scanned INTEGER NOT NULL
            );
        """)
    log.info(f"SQLite инициализирована: {config.DB_PATH}")


# ── AI Tips CRUD ──────────────────────────────────────────

def get_tips(target_role: str, limit: int = None) -> list[str]:
    """
    Последние N советов для роли (analyzer / order).
    Возвращает от новых к старым.
    """
    if limit is None:
        limit = config.MAX_TIPS_IN_PROMPT
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT tip FROM ai_tips WHERE target_role = ? "
            "ORDER BY id DESC LIMIT ?",
            (target_role, limit),
        ).fetchall()
    return [r["tip"] for r in rows]


def add_tip(target_role: str, tip: str, order_id: str, symbol: str):
    """Сохраняет совет."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO ai_tips (target_role, tip, source_order_id, source_symbol, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target_role, tip, order_id, symbol, datetime.now().isoformat()),
        )
    log.debug(f"Совет для {target_role}: {tip[:60]}...")


# ── Open Orders CRUD ─────────────────────────────────────

def add_open_order(
    order_id: str, symbol: str, direction: str, entry: float,
    sl: float, tp: float, qty: float, rr_ratio: float,
    mode: str, bybit_order_id: str = "", ai_reasoning: str = "",
):
    """Добавляет открытый ордер."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO open_orders "
            "(id, symbol, direction, entry, sl, tp, qty, rr_ratio, mode, "
            "bybit_order_id, created_at, ai_reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, symbol, direction, entry, sl, tp, qty, rr_ratio,
             mode, bybit_order_id, datetime.now().isoformat(), ai_reasoning),
        )
    log.info(f"Ордер {order_id[:8]} ({symbol} {direction}) добавлен в open_orders")


def get_open_orders() -> list[dict]:
    """Возвращает все открытые ордера."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM open_orders ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_order(order_id: str) -> Optional[dict]:
    """Возвращает открытый ордер по ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM open_orders WHERE id = ?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


def update_open_order_sl(order_id: str, new_sl: float, moved_to_be: bool = True):
    """Обновляет SL открытого ордера (для breakeven)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE open_orders SET sl = ?, sl_moved_to_be = ? WHERE id = ?",
            (new_sl, int(moved_to_be), order_id),
        )
    log.info(f"Ордер {order_id[:8]}: SL обновлён → {new_sl}")


def remove_open_order(order_id: str):
    """Удаляет ордер из open_orders."""
    with get_connection() as conn:
        conn.execute("DELETE FROM open_orders WHERE id = ?", (order_id,))


# ── Closed Orders CRUD ───────────────────────────────────

def close_order(
    order_id: str, symbol: str, direction: str, entry: float,
    sl: float, tp: float, qty: float, rr_ratio: float,
    mode: str, bybit_order_id: str, close_price: float,
    close_reason: str, pnl: float, created_at: str,
):
    """
    Переносит ордер из open_orders → closed_orders.
    Удаляет из open_orders, добавляет в closed_orders.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM open_orders WHERE id = ?", (order_id,))
        conn.execute(
            "INSERT INTO closed_orders "
            "(id, symbol, direction, entry, sl, tp, qty, rr_ratio, mode, "
            "bybit_order_id, close_price, close_reason, pnl, created_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, symbol, direction, entry, sl, tp, qty, rr_ratio,
             mode, bybit_order_id, close_price, close_reason, pnl,
             created_at, datetime.now().isoformat()),
        )
    log.info(f"Ордер {order_id[:8]} закрыт: {close_reason}, PnL={pnl:.2f}")


def get_closed_orders(limit: int = 50) -> list[dict]:
    """Последние N закрытых ордеров."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM closed_orders ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Lessons CRUD ──────────────────────────────────────────

def add_lesson(
    order_id: str, symbol: str, direction: str,
    entry: float, sl: float, tp: float,
    close_reason: str, pnl: float, mode: str,
    analysis: str, chart_snapshot: Optional[bytes] = None,
):
    """Добавляет урок."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO lessons "
            "(order_id, symbol, direction, entry, sl, tp, close_reason, pnl, "
            "mode, analysis, chart_snapshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, symbol, direction, entry, sl, tp, close_reason, pnl,
             mode, analysis, chart_snapshot, datetime.now().isoformat()),
        )
    log.info(f"Урок для {symbol} {direction} ({close_reason}) сохранён")


def count_lessons() -> int:
    """Количество уроков."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM lessons").fetchone()
    return row["cnt"]


def get_lesson_by_index(index: int) -> Optional[dict]:
    """
    Получает урок по индексу (1 = самый свежий).
    index 1 → новейший, index N → старейший.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM lessons ORDER BY id DESC LIMIT 1 OFFSET ?",
            (index - 1,),
        ).fetchone()
    return dict(row) if row else None


def get_lesson_order_details(order_id: str) -> Optional[dict]:
    """Получает детали ордера для урока (из closed_orders)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM closed_orders WHERE id = ?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Last Scan CRUD ────────────────────────────────────────

def save_scan_results(results: list[dict], total_scanned: int):
    """
    Сохраняет результаты скана (перезаписывает предыдущие).
    results: [{"symbol": ..., "direction": ..., "score_15m": ..., "score_1h": ..., "last_price": ...}]
    """
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("DELETE FROM last_scan")
        for r in results:
            conn.execute(
                "INSERT INTO last_scan "
                "(symbol, direction, score_15m, score_1h, last_price, scanned_at, total_scanned) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["symbol"], r["direction"], r["score_15m"], r["score_1h"],
                 r["last_price"], now, total_scanned),
            )
    log.debug(f"Результаты скана сохранены: {len(results)} монет")


def get_last_scan() -> tuple[list[dict], Optional[str], int]:
    """
    Возвращает (results, scanned_at, total_scanned).
    results отсортированы по score (min_score DESC).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM last_scan ORDER BY "
            "MIN(score_15m, score_1h) DESC"
        ).fetchall()

    if not rows:
        return [], None, 0

    results = [dict(r) for r in rows]
    scanned_at = results[0]["scanned_at"]
    total_scanned = results[0]["total_scanned"]
    return results, scanned_at, total_scanned


def generate_id() -> str:
    """Генерирует уникальный ID для ордера."""
    return str(uuid.uuid4())
