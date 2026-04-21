"""
CryptoRadar — Утилиты.
Экранирование Markdown V2 и вспомогательные функции.
"""

import re

# Символы, которые нужно экранировать в Telegram MarkdownV2
# https://core.telegram.org/bots/api#markdownv2-style
_MD_V2_SPECIAL = r'_*[]()~`>#+-=|{}.!'


def escape_md(text: str) -> str:
    """
    Экранирует ВСЕ спецсимволы для Telegram MarkdownV2.
    Используй для пользовательских данных (цены, символы монет и т.д.).
    НЕ используй для текста с уже вставленным форматированием.
    """
    result = []
    for char in str(text):
        if char in _MD_V2_SPECIAL:
            result.append(f'\\{char}')
        else:
            result.append(char)
    return ''.join(result)


def escape_md_keep_format(text: str) -> str:
    """
    Экранирует спецсимволы, НО сохраняет базовое форматирование:
    *bold*, _italic_, `code`, ```pre```.
    Удобно для финального текста, где нужно сохранить разметку.
    """
    # Спецсимволы, которые НЕ являются частью форматирования
    chars_to_escape = r'[]()~>#+-=|{}.!'
    result = []
    i = 0
    while i < len(text):
        char = text[i]
        # Пропускаем уже экранированные символы
        if char == '\\' and i + 1 < len(text):
            result.append(text[i:i+2])
            i += 2
            continue
        # Сохраняем ``` блоки как есть
        if text[i:i+3] == '```':
            end = text.find('```', i + 3)
            if end != -1:
                result.append(text[i:end+3])
                i = end + 3
                continue
        # Сохраняем ` inline code как есть
        if char == '`':
            end = text.find('`', i + 1)
            if end != -1:
                result.append(text[i:end+1])
                i = end + 1
                continue
        if char in chars_to_escape:
            result.append(f'\\{char}')
        else:
            result.append(char)
        i += 1
    return ''.join(result)


def format_price(price: float) -> str:
    """Форматирует цену с адекватной точностью."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    elif price >= 0.01:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


def format_volume(volume: float) -> str:
    """Форматирует объём в читаемый вид (K, M, B)."""
    if volume >= 1_000_000_000:
        return f"{volume / 1_000_000_000:.1f}B"
    elif volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    elif volume >= 1_000:
        return f"{volume / 1_000:.1f}K"
    return f"{volume:.0f}"


def truncate(text: str, max_len: int = 1024) -> str:
    """Обрезает текст до max_len символов, добавляя '...' если обрезано."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
