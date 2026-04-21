"""
CryptoRadar — Скринер.
Фильтрация монет: проверка совпадения направления на 15m и 1h,
подсчёт скоринга, отсев по порогу.
"""

from models import CoinData, ScreenResult, Signal, Direction
from indicators import compute_all
from logger import log
import config


def _count_direction(signals: list[Signal]) -> tuple[int, int]:
    """
    Считает количество LONG и SHORT сигналов.
    Возвращает (score_long, score_short).
    """
    long_count = sum(1 for s in signals if s.direction == Direction.LONG)
    short_count = sum(1 for s in signals if s.direction == Direction.SHORT)
    return long_count, short_count


def _determine_direction(long_count: int, short_count: int) -> Direction:
    """Определяет доминирующее направление."""
    if long_count > short_count:
        return Direction.LONG
    elif short_count > long_count:
        return Direction.SHORT
    return Direction.NEUTRAL


def screen_coin(coin: CoinData) -> ScreenResult:
    """
    Скринит одну монету:
    1. Расчёт 10 индикаторов на 15m и 1h
    2. Определение направления каждого ТФ
    3. Проверка совпадения направлений
    4. Проверка порога скоринга
    """
    result = ScreenResult(
        symbol=coin.symbol,
        last_price=coin.last_price,
        direction=Direction.NEUTRAL,
        score_15m=0,
        score_1h=0,
        passed=False,
    )

    # Считаем индикаторы на обоих таймфреймах
    if coin.klines_15m is None or coin.klines_1h is None:
        log.debug(f"{coin.symbol}: пропущена — нет данных на одном из ТФ")
        return result

    signals_15m = compute_all(coin.klines_15m)
    signals_1h = compute_all(coin.klines_1h)

    if not signals_15m or not signals_1h:
        log.debug(f"{coin.symbol}: пропущена — индикаторы не рассчитаны")
        return result

    result.signals_15m = signals_15m
    result.signals_1h = signals_1h

    # Считаем скоры LONG/SHORT на каждом ТФ
    long_15m, short_15m = _count_direction(signals_15m)
    long_1h, short_1h = _count_direction(signals_1h)

    dir_15m = _determine_direction(long_15m, short_15m)
    dir_1h = _determine_direction(long_1h, short_1h)

    # ── Проверка 1: направления должны совпадать ──
    if dir_15m == Direction.NEUTRAL or dir_1h == Direction.NEUTRAL:
        log.debug(f"{coin.symbol}: NEUTRAL на одном из ТФ (15m={dir_15m}, 1h={dir_1h})")
        return result

    if dir_15m != dir_1h:
        log.debug(f"{coin.symbol}: направления не совпадают (15m={dir_15m}, 1h={dir_1h})")
        return result

    # Направления совпали
    direction = dir_15m
    result.direction = direction

    # Скор = кол-во сигналов совпавшего направления
    if direction == Direction.LONG:
        result.score_15m = long_15m
        result.score_1h = long_1h
    else:
        result.score_15m = short_15m
        result.score_1h = short_1h

    # ── Проверка 2: минимальный скор на каждом ТФ ──
    min_score = result.min_score
    if min_score >= config.MIN_SCORE_THRESHOLD:
        result.passed = True
        log.info(
            f"✅ {coin.symbol} ПРОШЛА | {direction.value} | "
            f"Score: 15m={result.score_15m}, 1h={result.score_1h} (min={min_score})"
        )
    else:
        log.debug(
            f"❌ {coin.symbol} не прошла порог | {direction.value} | "
            f"Score: 15m={result.score_15m}, 1h={result.score_1h} (min={min_score} < {config.MIN_SCORE_THRESHOLD})"
        )

    return result


def screen_all(coins_data: list[CoinData]) -> list[ScreenResult]:
    """
    Скринит все монеты, возвращает только прошедшие.
    """
    log.info(f"Скрининг {len(coins_data)} монет...")
    passed = []

    for coin in coins_data:
        try:
            result = screen_coin(coin)
            if result.passed:
                passed.append(result)
        except Exception as e:
            log.error(f"Ошибка скрининга {coin.symbol}: {e}")
            continue

    log.info(f"Прошли скрининг: {len(passed)}/{len(coins_data)} монет")
    return passed
