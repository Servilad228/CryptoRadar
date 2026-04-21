"""
CryptoRadar — Модели данных.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class TradingMode(str, Enum):
    DEMO = "demo"
    BATTLE = "battle"


@dataclass
class Signal:
    """Один сигнал от индикатора."""
    name: str           # например: "RSI (14)"
    value: float        # текущее значение индикатора
    direction: Direction
    description: str    # человекочитаемое описание

    def __str__(self) -> str:
        arrow = "🟢" if self.direction == Direction.LONG else "🔴" if self.direction == Direction.SHORT else "⚪"
        return f"{arrow} {self.name}: {self.description}"


@dataclass
class CoinData:
    """Данные монеты с двух таймфреймов."""
    symbol: str
    volume_24h: float
    last_price: float
    klines_15m: Optional[pd.DataFrame] = None
    klines_1h: Optional[pd.DataFrame] = None


@dataclass
class ScreenResult:
    """Результат скрининга одной монеты."""
    symbol: str
    last_price: float
    direction: Direction          # финальное направление (совпавшее)
    score_15m: int                # бычий/медвежий скор на 15m
    score_1h: int                 # бычий/медвежий скор на 1h
    signals_15m: list[Signal] = field(default_factory=list)
    signals_1h: list[Signal] = field(default_factory=list)
    passed: bool = False          # прошла ли фильтр

    @property
    def min_score(self) -> int:
        return min(self.score_15m, self.score_1h)

    def signals_summary(self, tf: str = "both") -> str:
        """Текстовое описание сработавших сигналов."""
        lines = []
        if tf in ("15m", "both") and self.signals_15m:
            lines.append("📊 15m:")
            for s in self.signals_15m:
                if s.direction == self.direction:
                    lines.append(f"  {s}")
        if tf in ("1h", "both") and self.signals_1h:
            lines.append("📊 1h:")
            for s in self.signals_1h:
                if s.direction == self.direction:
                    lines.append(f"  {s}")
        return "\n".join(lines)


@dataclass
class OrderParams:
    """Параметры AI-сгенерированного ордера."""
    symbol: str
    direction: Direction
    entry: float
    sl: float
    tp: float
    qty: float
    rr_ratio: float
    reasoning: str          # объяснение от ORDER_MODEL


@dataclass
class Order:
    """Ордер (demo или battle)."""
    id: str
    symbol: str
    direction: Direction
    entry: float
    sl: float
    tp: float
    qty: float
    rr_ratio: float
    status: str              # open, filled, cancelled, closed
    mode: str                # demo | battle
    created_at: datetime
    bybit_order_id: str = ""
    pnl: float = 0.0
    sl_moved_to_be: bool = False
    close_reason: str = ""   # tp_hit / sl_hit / manual
    close_price: float = 0.0
    closed_at: Optional[datetime] = None
    ai_reasoning: str = ""   # логика AI при создании


