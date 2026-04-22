"""
CryptoRadar — Сканер Bybit.
Получение топ-30 по объёмам, загрузка свечных данных (OHLCV).
"""

import time
from typing import Optional

import pandas as pd
from pybit.unified_trading import HTTP

import config
from logger import log
from models import CoinData


def _create_session() -> HTTP:
    """Создаёт HTTP-сессию Bybit API v5."""
    kwargs = {"testnet": False}
    if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
        kwargs["api_key"] = config.BYBIT_API_KEY
        kwargs["api_secret"] = config.BYBIT_API_SECRET
    return HTTP(**kwargs)


_session: Optional[HTTP] = None


def _get_session() -> HTTP:
    global _session
    if _session is None:
        _session = _create_session()
    return _session


def get_top_coins(n: int = 30) -> list[dict]:
    """
    Получает все тикеры категории linear, сортирует по volume24h,
    возвращает топ-N в виде [{"symbol": ..., "volume24h": ..., "lastPrice": ...}].
    """
    session = _get_session()
    log.info(f"Загружаю тикеры Bybit ({config.CATEGORY})...")

    response = session.get_tickers(category=config.CATEGORY)
    tickers = response["result"]["list"]

    # Фильтруем только USDT пары
    usdt_tickers = [t for t in tickers if t["symbol"].endswith("USDT")]

    # Сортируем по 24h объёму (строки → float)
    usdt_tickers.sort(key=lambda t: float(t["volume24h"]), reverse=True)

    top = usdt_tickers[:n]
    log.info(f"Топ-{n} монет по объёмам: {[t['symbol'] for t in top[:5]]}... ")

    return [
        {
            "symbol": t["symbol"],
            "volume24h": float(t["volume24h"]),
            "lastPrice": float(t["lastPrice"]),
        }
        for t in top
    ]


def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """
    Загружает свечные данные (OHLCV) для символа.
    
    Возвращает DataFrame с колонками:
    timestamp, open, high, low, close, volume
    
    Свечи отсортированы хронологически (старые → новые).
    """
    session = _get_session()

    response = session.get_kline(
        category=config.CATEGORY,
        symbol=symbol,
        interval=interval,
        limit=limit,
    )

    raw = response["result"]["list"]

    if not raw:
        log.warning(f"Нет свечей для {symbol} (interval={interval})")
        return pd.DataFrame()

    # Bybit возвращает: [startTime, open, high, low, close, volume, turnover]
    # В обратном хронологическом порядке (новые первые) → разворачиваем
    df = pd.DataFrame(
        reversed(raw),
        columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
    )

    # Конвертируем типы
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df.set_index("timestamp", inplace=True)
    df.drop(columns=["turnover"], inplace=True)

    return df


def scan_all(coins: list[dict]) -> list[CoinData]:
    """
    Загружает свечи для всех монет на обоих таймфреймах (15m + 1h).
    Возвращает список CoinData.
    """
    results = []

    for i, coin in enumerate(coins):
        if config.CANCEL_SCAN:
            log.info("Скан прерван по запросу пользователя (scanner).")
            break

        symbol = coin["symbol"]
        log.debug(f"[{i+1}/{len(coins)}] Загружаю свечи: {symbol}")

        try:
            klines_15m = get_klines(symbol, interval="15", limit=config.KLINE_LIMIT)
            time.sleep(config.API_REQUEST_DELAY)

            klines_1h = get_klines(symbol, interval="60", limit=config.KLINE_LIMIT)
            time.sleep(config.API_REQUEST_DELAY)

            cd = CoinData(
                symbol=symbol,
                volume_24h=coin["volume24h"],
                last_price=coin["lastPrice"],
                klines_15m=klines_15m if not klines_15m.empty else None,
                klines_1h=klines_1h if not klines_1h.empty else None,
            )
            results.append(cd)

        except Exception as e:
            log.error(f"Ошибка загрузки свечей для {symbol}: {e}")
            continue

    log.info(f"Загружено свечей для {len(results)}/{len(coins)} монет")
    return results
