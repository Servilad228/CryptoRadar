"""
CryptoRadar — Утилиты.
Форматирование цен, объёмов и вспомогательные функции.
"""


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
