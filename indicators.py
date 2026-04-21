"""
CryptoRadar — 10 технических индикаторов.
Использует библиотеку `ta` (Technical Analysis Library).
Каждый индикатор возвращает Signal с направлением LONG/SHORT/NEUTRAL.
"""

import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.trend import MACD, EMAIndicator, ADXIndicator
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice

import config
from models import Signal, Direction


def compute_all(df: pd.DataFrame) -> list[Signal]:
    """
    Рассчитывает все 10 индикаторов для DataFrame (OHLCV).
    Возвращает список Signal (LONG/SHORT/NEUTRAL для каждого).
    """
    if df is None or df.empty or len(df) < 50:
        return []

    signals = []
    signals.append(_rsi(df))
    signals.append(_macd(df))
    signals.append(_ema_cross(df))
    signals.append(_price_vs_ema200(df))
    signals.append(_bollinger_breakout(df))
    signals.append(_volume_spike(df))
    signals.append(_obv_trend(df))
    signals.append(_stoch_rsi(df))
    signals.append(_adx(df))
    signals.append(_vwap(df))

    return signals


# ──────────────────────────────────────────────────────────
# 1. RSI (14)
# ──────────────────────────────────────────────────────────
def _rsi(df: pd.DataFrame) -> Signal:
    try:
        rsi_ind = RSIIndicator(close=df["close"], window=config.RSI_PERIOD)
        rsi = rsi_ind.rsi()

        if rsi is None or rsi.empty or rsi.isna().all():
            return Signal("RSI (14)", 0, Direction.NEUTRAL, "нет данных")

        val = rsi.iloc[-1]
        prev = rsi.iloc[-2] if len(rsi) > 1 else val

        if pd.isna(val):
            return Signal("RSI (14)", 0, Direction.NEUTRAL, "нет данных")

        if val < config.RSI_OVERSOLD or (prev < 30 and val > 30):
            return Signal("RSI (14)", round(val, 1), Direction.LONG,
                          f"RSI={val:.1f} — перепроданность, потенциал разворота вверх")
        elif val > config.RSI_OVERBOUGHT or (prev > 70 and val < 70):
            return Signal("RSI (14)", round(val, 1), Direction.SHORT,
                          f"RSI={val:.1f} — перекупленность, потенциал разворота вниз")
        return Signal("RSI (14)", round(val, 1), Direction.NEUTRAL,
                      f"RSI={val:.1f} — нейтральная зона")
    except Exception as e:
        return Signal("RSI (14)", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 2. MACD Crossover
# ──────────────────────────────────────────────────────────
def _macd(df: pd.DataFrame) -> Signal:
    try:
        macd_ind = MACD(
            close=df["close"],
            window_fast=config.MACD_FAST,
            window_slow=config.MACD_SLOW,
            window_sign=config.MACD_SIGNAL,
        )
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()

        if macd_line is None or signal_line is None:
            return Signal("MACD", 0, Direction.NEUTRAL, "нет данных")

        m = macd_line.iloc[-1]
        s = signal_line.iloc[-1]
        prev_m = macd_line.iloc[-2] if len(macd_line) > 1 else m
        prev_s = signal_line.iloc[-2] if len(signal_line) > 1 else s

        if pd.isna(m) or pd.isna(s):
            return Signal("MACD", 0, Direction.NEUTRAL, "нет данных")

        # Бычий кроссовер
        if prev_m <= prev_s and m > s:
            return Signal("MACD", round(m, 4), Direction.LONG,
                          "MACD пересёк Signal снизу вверх — бычий импульс")
        # Медвежий кроссовер
        elif prev_m >= prev_s and m < s:
            return Signal("MACD", round(m, 4), Direction.SHORT,
                          "MACD пересёк Signal сверху вниз — медвежий импульс")

        if m > s:
            return Signal("MACD", round(m, 4), Direction.LONG,
                          "MACD выше Signal — бычий тренд")
        elif m < s:
            return Signal("MACD", round(m, 4), Direction.SHORT,
                          "MACD ниже Signal — медвежий тренд")
        return Signal("MACD", round(m, 4), Direction.NEUTRAL, "MACD ≈ Signal")
    except Exception as e:
        return Signal("MACD", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 3. EMA Cross (12/26)
# ──────────────────────────────────────────────────────────
def _ema_cross(df: pd.DataFrame) -> Signal:
    try:
        ema_fast = EMAIndicator(close=df["close"], window=config.EMA_FAST).ema_indicator()
        ema_slow = EMAIndicator(close=df["close"], window=config.EMA_SLOW).ema_indicator()

        if ema_fast is None or ema_slow is None:
            return Signal("EMA Cross", 0, Direction.NEUTRAL, "нет данных")

        fast_val = ema_fast.iloc[-1]
        slow_val = ema_slow.iloc[-1]

        if pd.isna(fast_val) or pd.isna(slow_val):
            return Signal("EMA Cross", 0, Direction.NEUTRAL, "нет данных")

        if fast_val > slow_val:
            return Signal("EMA Cross (12/26)", round(fast_val, 4), Direction.LONG,
                          f"EMA-12 > EMA-26 — бычий")
        elif fast_val < slow_val:
            return Signal("EMA Cross (12/26)", round(fast_val, 4), Direction.SHORT,
                          f"EMA-12 < EMA-26 — медвежий")
        return Signal("EMA Cross (12/26)", round(fast_val, 4), Direction.NEUTRAL, "EMA-12 ≈ EMA-26")
    except Exception as e:
        return Signal("EMA Cross", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 4. Price vs EMA-200
# ──────────────────────────────────────────────────────────
def _price_vs_ema200(df: pd.DataFrame) -> Signal:
    try:
        if len(df) < config.EMA_TREND:
            return Signal("EMA-200", 0, Direction.NEUTRAL, f"нужно ≥{config.EMA_TREND} свечей")

        ema200 = EMAIndicator(close=df["close"], window=config.EMA_TREND).ema_indicator()

        if ema200 is None or ema200.isna().all():
            return Signal("EMA-200", 0, Direction.NEUTRAL, "нет данных")

        price = df["close"].iloc[-1]
        ema_val = ema200.iloc[-1]

        if pd.isna(ema_val):
            return Signal("EMA-200", 0, Direction.NEUTRAL, "нет данных")

        diff_pct = ((price - ema_val) / ema_val) * 100

        if price > ema_val:
            return Signal("Price vs EMA-200", round(diff_pct, 2), Direction.LONG,
                          f"Цена на {diff_pct:.1f}% выше EMA-200 — глобальный бычий тренд")
        elif price < ema_val:
            return Signal("Price vs EMA-200", round(diff_pct, 2), Direction.SHORT,
                          f"Цена на {abs(diff_pct):.1f}% ниже EMA-200 — глобальный медвежий тренд")
        return Signal("Price vs EMA-200", 0, Direction.NEUTRAL, "Цена ≈ EMA-200")
    except Exception as e:
        return Signal("EMA-200", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 5. Bollinger Bands Breakout
# ──────────────────────────────────────────────────────────
def _bollinger_breakout(df: pd.DataFrame) -> Signal:
    try:
        bb = BollingerBands(
            close=df["close"],
            window=config.BB_PERIOD,
            window_dev=config.BB_STD,
        )
        upper = bb.bollinger_hband().iloc[-1]
        lower = bb.bollinger_lband().iloc[-1]
        price = df["close"].iloc[-1]

        if pd.isna(upper) or pd.isna(lower):
            return Signal("Bollinger Bands", 0, Direction.NEUTRAL, "нет данных")

        if price > upper:
            return Signal("Bollinger Breakout", round(price, 4), Direction.LONG,
                          f"Цена пробила верхнюю BB ({upper:.4f}) — бычий пробой")
        elif price < lower:
            return Signal("Bollinger Breakout", round(price, 4), Direction.SHORT,
                          f"Цена пробила нижнюю BB ({lower:.4f}) — медвежий пробой")
        return Signal("Bollinger Bands", round(price, 4), Direction.NEUTRAL,
                      "Цена внутри полос Боллинджера")
    except Exception as e:
        return Signal("Bollinger Bands", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 6. Volume Spike
# ──────────────────────────────────────────────────────────
def _volume_spike(df: pd.DataFrame) -> Signal:
    try:
        vol = df["volume"]
        vol_ma = vol.rolling(window=config.VOLUME_MA_PERIOD).mean()

        if pd.isna(vol_ma.iloc[-1]) or vol_ma.iloc[-1] == 0:
            return Signal("Volume Spike", 0, Direction.NEUTRAL, "нет данных")

        current_vol = vol.iloc[-1]
        avg_vol = vol_ma.iloc[-1]
        ratio = current_vol / avg_vol

        if ratio >= config.VOLUME_SPIKE_MULTIPLIER:
            is_green = df["close"].iloc[-1] > df["open"].iloc[-1]
            direction = Direction.LONG if is_green else Direction.SHORT
            color = "зелёная" if is_green else "красная"
            return Signal("Volume Spike", round(ratio, 1), direction,
                          f"Объём в {ratio:.1f}x выше среднего + {color} свеча")

        return Signal("Volume Spike", round(ratio, 1), Direction.NEUTRAL,
                      f"Объём {ratio:.1f}x от среднего — нормальный")
    except Exception as e:
        return Signal("Volume Spike", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 7. OBV Trend
# ──────────────────────────────────────────────────────────
def _obv_trend(df: pd.DataFrame) -> Signal:
    try:
        obv_ind = OnBalanceVolumeIndicator(close=df["close"], volume=df["volume"])
        obv = obv_ind.on_balance_volume()

        if obv is None or len(obv) < config.OBV_TREND_BARS:
            return Signal("OBV", 0, Direction.NEUTRAL, "нет данных")

        last_n = obv.iloc[-config.OBV_TREND_BARS:]
        diffs = last_n.diff().dropna()

        if len(diffs) == 0:
            return Signal("OBV", 0, Direction.NEUTRAL, "нет данных")

        all_up = (diffs > 0).all()
        all_down = (diffs < 0).all()

        if all_up:
            return Signal("OBV Trend", round(obv.iloc[-1], 0), Direction.LONG,
                          f"OBV растёт {config.OBV_TREND_BARS} баров подряд — деньги втекают")
        elif all_down:
            return Signal("OBV Trend", round(obv.iloc[-1], 0), Direction.SHORT,
                          f"OBV падает {config.OBV_TREND_BARS} баров подряд — деньги утекают")
        return Signal("OBV Trend", round(obv.iloc[-1], 0), Direction.NEUTRAL, "OBV без чёткого направления")
    except Exception as e:
        return Signal("OBV", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 8. Stochastic RSI
# ──────────────────────────────────────────────────────────
def _stoch_rsi(df: pd.DataFrame) -> Signal:
    try:
        stoch = StochRSIIndicator(
            close=df["close"],
            window=config.STOCH_RSI_PERIOD,
            smooth1=3,
            smooth2=3,
        )
        k = stoch.stochrsi_k()
        d = stoch.stochrsi_d()

        if k is None or d is None:
            return Signal("StochRSI", 0, Direction.NEUTRAL, "нет данных")

        k_val = k.iloc[-1] * 100  # ta library возвращает 0-1, приводим к 0-100
        d_val = d.iloc[-1] * 100
        prev_k = (k.iloc[-2] * 100) if len(k) > 1 else k_val
        prev_d = (d.iloc[-2] * 100) if len(d) > 1 else d_val

        if pd.isna(k_val) or pd.isna(d_val):
            return Signal("StochRSI", 0, Direction.NEUTRAL, "нет данных")

        if k_val < config.STOCH_RSI_OVERSOLD and prev_k <= prev_d and k_val > d_val:
            return Signal("StochRSI", round(k_val, 1), Direction.LONG,
                          f"StochRSI K={k_val:.1f} < 20, бычий кросс — сигнал покупки")
        elif k_val > config.STOCH_RSI_OVERBOUGHT and prev_k >= prev_d and k_val < d_val:
            return Signal("StochRSI", round(k_val, 1), Direction.SHORT,
                          f"StochRSI K={k_val:.1f} > 80, медвежий кросс — сигнал продажи")

        if k_val < config.STOCH_RSI_OVERSOLD:
            return Signal("StochRSI", round(k_val, 1), Direction.LONG,
                          f"StochRSI K={k_val:.1f} — зона перепроданности")
        elif k_val > config.STOCH_RSI_OVERBOUGHT:
            return Signal("StochRSI", round(k_val, 1), Direction.SHORT,
                          f"StochRSI K={k_val:.1f} — зона перекупленности")

        return Signal("StochRSI", round(k_val, 1), Direction.NEUTRAL,
                      f"StochRSI K={k_val:.1f} — нейтральная зона")
    except Exception as e:
        return Signal("StochRSI", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 9. ADX (14)
# ──────────────────────────────────────────────────────────
def _adx(df: pd.DataFrame) -> Signal:
    try:
        adx_ind = ADXIndicator(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            window=config.ADX_PERIOD,
        )
        adx_val = adx_ind.adx().iloc[-1]
        dmp = adx_ind.adx_pos().iloc[-1]
        dmn = adx_ind.adx_neg().iloc[-1]

        if pd.isna(adx_val):
            return Signal("ADX", 0, Direction.NEUTRAL, "нет данных")

        if adx_val > config.ADX_THRESHOLD:
            if dmp > dmn:
                return Signal("ADX", round(adx_val, 1), Direction.LONG,
                              f"ADX={adx_val:.1f}, DI+={dmp:.1f} > DI-={dmn:.1f} — бычий тренд")
            elif dmn > dmp:
                return Signal("ADX", round(adx_val, 1), Direction.SHORT,
                              f"ADX={adx_val:.1f}, DI-={dmn:.1f} > DI+={dmp:.1f} — медвежий тренд")

        return Signal("ADX", round(adx_val, 1), Direction.NEUTRAL,
                      f"ADX={adx_val:.1f} — слабый/нет тренда")
    except Exception as e:
        return Signal("ADX", 0, Direction.NEUTRAL, f"ошибка: {e}")


# ──────────────────────────────────────────────────────────
# 10. VWAP Position
# ──────────────────────────────────────────────────────────
def _vwap(df: pd.DataFrame) -> Signal:
    try:
        vwap_ind = VolumeWeightedAveragePrice(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            volume=df["volume"],
        )
        vwap_series = vwap_ind.volume_weighted_average_price()

        if vwap_series is None or vwap_series.isna().all():
            return Signal("VWAP", 0, Direction.NEUTRAL, "нет данных")

        price = df["close"].iloc[-1]
        vwap_val = vwap_series.iloc[-1]

        if pd.isna(vwap_val):
            return Signal("VWAP", 0, Direction.NEUTRAL, "нет данных")

        diff_pct = ((price - vwap_val) / vwap_val) * 100

        if price > vwap_val:
            return Signal("VWAP", round(diff_pct, 2), Direction.LONG,
                          f"Цена на {diff_pct:.2f}% выше VWAP — покупатели доминируют")
        elif price < vwap_val:
            return Signal("VWAP", round(abs(diff_pct), 2), Direction.SHORT,
                          f"Цена на {abs(diff_pct):.2f}% ниже VWAP — продавцы доминируют")
        return Signal("VWAP", 0, Direction.NEUTRAL, "Цена ≈ VWAP")
    except Exception as e:
        return Signal("VWAP", 0, Direction.NEUTRAL, f"ошибка: {e}")
