"""
CryptoRadar — Модели данных (Pydantic).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import pandas as pd


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


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
class AnalysisResult:
    """Финальный результат: скрининг + AI + график."""
    screen: ScreenResult
    ai_summary: str = ""          # краткое резюме (3-7 предложений)
    ai_details: str = ""          # полный анализ
    chart_bytes: Optional[bytes] = None
