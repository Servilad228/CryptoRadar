"""Тесты модуля ai_analyst.py"""

import pytest
from ai_analyst import _extract_levels, estimate_probability
from models import ScreenResult, Direction, Signal

def test_extract_levels_ru():
    """Тест парсинга уровней на русском"""
    text = \"\"\"
    📝 РЕЗЮМЕ
    Бла бла бла
    
    📊 КЛЮЧЕВЫЕ УРОВНИ
    - Поддержка: 123.45
    - Сопротивление: 150.00
    \"\"\"
    levels = _extract_levels(text)
    assert levels.get("support") == 123.45
    assert levels.get("resistance") == 150.0

def test_extract_levels_en_and_symbols():
    """Тест парсинга уровней с запятыми и английскими ключами"""
    text = \"\"\"
    Key levels:
    support: 1,234.56
    Resistance  :  2000
    \"\"\"
    levels = _extract_levels(text)
    assert levels.get("support") == 1234.56
    assert levels.get("resistance") == 2000.0

def test_extract_levels_missing():
    """Тест когда уровней нет в тексте"""
    text = "Просто текст без уровней."
    levels = _extract_levels(text)
    assert levels == {}

def test_estimate_probability_base():
    """Проверка базового расчета вероятности"""
    screen = ScreenResult(
        symbol="BTCUSDT",
        last_price=100.0,
        direction=Direction.LONG,
        score_15m=3,
        score_1h=4,
    )
    # min_score = 3 -> base = 30
    # нет индикаторов, значит bonus = 0 -> 30%
    prob = estimate_probability(screen)
    assert prob == 30

def test_estimate_probability_with_bonuses():
    """Проверка расчета вероятности с бонусами совпадения"""
    screen = ScreenResult(
        symbol="BTCUSDT",
        last_price=100.0,
        direction=Direction.LONG,
        score_15m=5,
        score_1h=5,
    )
    # Совпадение 2 индикаторов (RSI, MACD)
    screen.signals_15m = [
        Signal("RSI", 1, Direction.LONG, "buy"),
        Signal("MACD", 1, Direction.LONG, "buy")
    ]
    screen.signals_1h = [
        Signal("RSI", 1, Direction.LONG, "buy"),
        Signal("MACD", 1, Direction.LONG, "buy")
    ]
    
    # min_score = 5 -> base = 50
    # 2 совпадения = 2 * 5 = +10
    # Итого: 60
    prob = estimate_probability(screen)
    assert prob == 60

def test_estimate_probability_adx_bonus():
    """Проверка бонуса за сильный ADX"""
    screen = ScreenResult(
        symbol="BTCUSDT",
        last_price=100.0,
        direction=Direction.LONG,
        score_15m=5,
        score_1h=5,
    )
    screen.signals_1h = [
        Signal("ADX", 30.0, Direction.LONG, "strong trend")
    ]
    
    # min_score = 5 -> base = 50
    # ADX > 25 бонус +10
    # Итого: 60
    prob = estimate_probability(screen)
    assert prob == 60
