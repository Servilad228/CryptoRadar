"""Тесты модуля telegram_bot.py"""

import pytest
from telegram_bot import _split_text

def test_split_text_short():
    """Тест текста, который не требует нарезки"""
    text = "Короткий текст"
    chunks = _split_text(text, max_len=100)
    assert len(chunks) == 1
    assert chunks[0] == "Короткий текст"

def test_split_text_long_without_newlines():
    """Тест длинного цельного текста (без переносов строк)"""
    text = "A" * 5000
    chunks = _split_text(text, max_len=4096)
    
    assert len(chunks) == 2
    assert len(chunks[0]) == 4096
    assert chunks[0] == "A" * 4096
    assert len(chunks[1]) == 904
    assert chunks[1] == "A" * 904

def test_split_text_with_newlines():
    """Тест умной нарезки по переносам строк"""
    # Создаем текст из двух частей, разделенных переносом строки.
    # Первая часть - 4000 символов. Вторая - 1000 символов.
    part1 = "A" * 4000
    part2 = "B" * 1000
    text = f"{part1}\n{part2}"
    
    chunks = _split_text(text, max_len=4096)
    
    # Резка должна произойти ровно по \n, чтобы не разорвать слово B.
    assert len(chunks) == 2
    assert chunks[0] == part1
    assert chunks[1] == part2
