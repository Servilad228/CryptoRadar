"""Тесты модуля order_ai.py"""

import pytest
import config
from models import OrderParams, Direction
from order_ai import validate_order

def test_validate_order_long_correct():
    """Проверка правильного LONG ордера"""
    config.RR_MIN = 1.5
    config.RR_MAX = 3.0
    config.TARGET_PROFIT_USD = 15.0

    # Вход: 100, SL: 90 (Риск 10), TP: 120 (Профит 20). R/R = 2.0
    params = OrderParams(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry=100.0,
        sl=90.0,
        tp=120.0,
        qty=0.75,  # 0.75 * 20 = 15.0 profit
        rr_ratio=2.0,
        reasoning="Test"
    )
    
    is_valid, errors = validate_order(params, "LONG", 100.0)
    assert is_valid is True
    assert not errors

def test_validate_order_short_correct():
    """Проверка правильного SHORT ордера"""
    config.RR_MIN = 1.5
    config.RR_MAX = 3.0
    config.TARGET_PROFIT_USD = 15.0

    # Вход: 100, SL: 110 (Риск 10), TP: 80 (Профит 20). R/R = 2.0
    params = OrderParams(
        symbol="BTCUSDT",
        direction=Direction.SHORT,
        entry=100.0,
        sl=110.0,
        tp=80.0,
        qty=0.75,  # 0.75 * 20 = 15.0 profit
        rr_ratio=2.0,
        reasoning="Test"
    )
    
    is_valid, errors = validate_order(params, "SHORT", 100.0)
    assert is_valid is True
    assert not errors

def test_validate_order_wrong_sl_tp():
    """Проверка неверных уровней SL и TP для направлений"""
    config.TARGET_PROFIT_USD = 15.0

    # LONG со стопом ВЫШЕ цены (ошибка)
    params_long = OrderParams("BTC", Direction.LONG, 100.0, 110.0, 120.0, 1.0, 2.0, "")
    is_valid, errs = validate_order(params_long, "LONG", 100.0)
    assert is_valid is False
    assert any("ниже Entry" in e for e in errs)

    # SHORT с тейком ВЫШЕ цены (ошибка)
    params_short = OrderParams("BTC", Direction.SHORT, 100.0, 110.0, 120.0, 1.0, 2.0, "")
    is_valid, errs = validate_order(params_short, "SHORT", 100.0)
    assert is_valid is False
    assert any("ниже Entry" in e for e in errs)

def test_validate_order_rr_out_of_bounds():
    """Проверка выхода R/R за границы"""
    config.RR_MIN = 1.5
    config.RR_MAX = 3.0
    
    # R/R = 1.0 (ниже минимума)
    params = OrderParams("BTC", Direction.LONG, 100.0, 90.0, 110.0, 1.5, 1.0, "")
    is_valid, errs = validate_order(params, "LONG", 100.0)
    assert is_valid is False
    assert any("ниже минимума" in e for e in errs)

    # R/R = 4.0 (выше максимума)
    params2 = OrderParams("BTC", Direction.LONG, 100.0, 90.0, 140.0, 0.375, 4.0, "")
    is_valid, errs2 = validate_order(params2, "LONG", 100.0)
    assert is_valid is False
    assert any("выше максимума" in e for e in errs2)

def test_validate_order_negative_qty():
    """Проверка количества <= 0"""
    params = OrderParams("BTC", Direction.LONG, 100.0, 90.0, 120.0, 0.0, 2.0, "")
    is_valid, errs = validate_order(params, "LONG", 100.0)
    assert is_valid is False
    assert any("положительным" in e for e in errs)

def test_validate_order_price_deviation():
    """Проверка защиты от отклонения >2% цены входа от текущей цены"""
    # Текущая цена 100. Пытаемся зайти по 105 (+5%)
    params = OrderParams("BTC", Direction.LONG, 105.0, 95.0, 125.0, 0.5, 2.0, "")
    is_valid, errs = validate_order(params, "LONG", 100.0)
    assert is_valid is False
    assert any("отклоняется от текущей" in e for e in errs)
