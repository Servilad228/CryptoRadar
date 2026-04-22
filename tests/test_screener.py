"""Тесты модуля indicators.py и screener.py"""

import pytest
import pandas as pd
import numpy as np

import config
from indicators import _rsi, _adx
from screener import _determine_direction
from models import Direction

def test_screener_determine_direction():
    """Тест определения доминирующего направления"""
    # 5 Long, 2 Short -> LONG
    assert _determine_direction(5, 2) == Direction.LONG
    # 2 Long, 6 Short -> SHORT
    assert _determine_direction(2, 6) == Direction.SHORT
    # 3 Long, 3 Short -> NEUTRAL
    assert _determine_direction(3, 3) == Direction.NEUTRAL
    # 0 Long, 0 Short -> NEUTRAL
    assert _determine_direction(0, 0) == Direction.NEUTRAL

def test_indicator_rsi_oversold():
    """Тест RSI: перепроданность (<30) должна давать LONG"""
    # Создаем фейковый DataFrame цены, падающий
    prices = np.linspace(100, 50, 20)
    df = pd.DataFrame({"close": prices})
    
    signal = _rsi(df)
    # При падении RSI будет низким
    assert signal.direction == Direction.LONG
    assert "перепроданность" in signal.reasoning

def test_indicator_rsi_overbought():
    """Тест RSI: перекупленность (>70) должна давать SHORT"""
    # Создаем фейковый DataFrame цены, растущий
    prices = np.linspace(50, 150, 20)
    df = pd.DataFrame({"close": prices})
    
    signal = _rsi(df)
    # При росте RSI будет высоким
    assert signal.direction == Direction.SHORT
    assert "перекупленность" in signal.reasoning

def test_indicator_adx_weak_trend():
    """Тест ADX: слабый тренд (<25) должен игнорироваться (NEUTRAL)"""
    # Флэт цена (высокий хай, низкий лоу, закрытие посередине)
    df = pd.DataFrame({
        "high": [105] * 20,
        "low": [95] * 20,
        "close": [100] * 20
    })
    
    signal = _adx(df)
    # Нет направленного движения, ADX низкий
    assert signal.direction == Direction.NEUTRAL
    assert "слабый" in signal.reasoning or "флэт" in signal.reasoning

