"""
CryptoRadar — Генерация графиков.
Candlestick-графики с overlays (EMA, BB) и subplots (Volume, RSI, MACD).
"""

import io

import matplotlib
matplotlib.use("Agg")  # headless рендеринг

import mplfinance as mpf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands

import config
from logger import log
from models import ScreenResult, Direction


# Кастомная тёмная тема
_DARK_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#00c853",
        down="#ff1744",
        edge="inherit",
        wick="inherit",
        volume={"up": "#00c853", "down": "#ff1744"},
    ),
    figcolor="#0d1117",
    facecolor="#0d1117",
    gridcolor="#21262d",
    gridstyle="--",
    gridaxis="both",
    y_on_right=True,
    rc={
        "font.size": 9,
        "axes.labelcolor": "#8b949e",
        "xtick.color": "#8b949e",
        "ytick.color": "#8b949e",
    },
)


def generate(screen: ScreenResult, klines_df: pd.DataFrame, levels: dict = None) -> bytes:
    """
    Генерирует candlestick-график с overlays и subplots.
    levels: {"support": float, "resistance": float} — горизонтальные линии.
    Возвращает PNG в виде bytes.
    """
    if klines_df is None or klines_df.empty:
        raise ValueError(f"Нет данных для графика {screen.symbol}")

    # Берём последние 100 свечей для читаемости
    df = klines_df.tail(100).copy()

    # ── Overlays ──
    addplots = []

    # EMA-12
    try:
        ema12 = EMAIndicator(close=df["close"], window=config.EMA_FAST).ema_indicator()
        if ema12 is not None and not ema12.isna().all():
            addplots.append(mpf.make_addplot(ema12, color="#42a5f5", width=1))
    except Exception:
        pass

    # EMA-26
    try:
        ema26 = EMAIndicator(close=df["close"], window=config.EMA_SLOW).ema_indicator()
        if ema26 is not None and not ema26.isna().all():
            addplots.append(mpf.make_addplot(ema26, color="#ffa726", width=1))
    except Exception:
        pass

    # Bollinger Bands
    try:
        bb = BollingerBands(close=df["close"], window=config.BB_PERIOD, window_dev=config.BB_STD)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        if bb_upper is not None and not bb_upper.isna().all():
            addplots.append(mpf.make_addplot(bb_upper, color="#78909c", width=0.7, linestyle=":"))
            addplots.append(mpf.make_addplot(bb_lower, color="#78909c", width=0.7, linestyle=":"))
    except Exception:
        pass

    # ── Support / Resistance levels ──
    if levels:
        if "support" in levels and levels["support"]:
            try:
                support_line = pd.Series([levels["support"]] * len(df), index=df.index)
                addplots.append(mpf.make_addplot(
                    support_line, color="#4caf50", width=1.2, linestyle="--"
                ))
            except Exception:
                pass
        if "resistance" in levels and levels["resistance"]:
            try:
                resistance_line = pd.Series([levels["resistance"]] * len(df), index=df.index)
                addplots.append(mpf.make_addplot(
                    resistance_line, color="#f44336", width=1.2, linestyle="--"
                ))
            except Exception:
                pass

    # ── RSI subplot ──
    has_rsi = False
    try:
        rsi = RSIIndicator(close=df["close"], window=config.RSI_PERIOD).rsi()
        if rsi is not None and not rsi.isna().all():
            addplots.append(mpf.make_addplot(rsi, panel=2, color="#26c6da", width=1, ylabel="RSI"))
            rsi_30 = pd.Series([30] * len(df), index=df.index)
            rsi_70 = pd.Series([70] * len(df), index=df.index)
            addplots.append(mpf.make_addplot(rsi_30, panel=2, color="#4caf50", width=0.5, linestyle="--"))
            addplots.append(mpf.make_addplot(rsi_70, panel=2, color="#f44336", width=0.5, linestyle="--"))
            has_rsi = True
    except Exception:
        pass

    # ── MACD subplot ──
    has_macd = False
    try:
        macd_ind = MACD(
            close=df["close"],
            window_fast=config.MACD_FAST,
            window_slow=config.MACD_SLOW,
            window_sign=config.MACD_SIGNAL,
        )
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        hist = macd_ind.macd_diff()

        macd_panel = 3 if has_rsi else 2

        if macd_line is not None and not macd_line.isna().all():
            addplots.append(mpf.make_addplot(macd_line, panel=macd_panel, color="#42a5f5", width=1, ylabel="MACD"))
            addplots.append(mpf.make_addplot(signal_line, panel=macd_panel, color="#ffa726", width=1))

            if hist is not None and not hist.isna().all():
                hist_colors = ["#00c853" if v >= 0 else "#ff1744" for v in hist.fillna(0)]
                addplots.append(mpf.make_addplot(hist, panel=macd_panel, type="bar", color=hist_colors, width=0.7))
            has_macd = True
    except Exception:
        pass

    # ── Заголовок ──
    dir_emoji = "[LONG]" if screen.direction == Direction.LONG else "[SHORT]"
    title = f"{screen.symbol}  |  {dir_emoji}  |  Score: 15m={screen.score_15m} / 1h={screen.score_1h}"

    # ── Определяем panel_ratios ──
    if has_rsi and has_macd:
        ratios = (6, 2, 2, 2)
    elif has_rsi or has_macd:
        ratios = (6, 2, 2)
    else:
        ratios = (6, 2)

    # ── Рендер ──
    buf = io.BytesIO()

    try:
        fig, axes = mpf.plot(
            df,
            type="candle",
            style=_DARK_STYLE,
            title=title,
            volume=True,
            addplot=addplots if addplots else None,
            figsize=(14, 10),
            tight_layout=True,
            returnfig=True,
            panel_ratios=ratios,
            scale_padding={"left": 0.3, "right": 1.0, "top": 0.6, "bottom": 0.5},
        )

        # Подписи уровней S/R
        if levels:
            ax_main = axes[0]
            if "support" in levels and levels["support"]:
                ax_main.annotate(
                    f"S: {levels['support']}", xy=(0.01, 0), xycoords='axes fraction',
                    xytext=(5, 0), textcoords='offset points',
                    color="#4caf50", fontsize=8, fontweight="bold",
                    verticalalignment='bottom',
                    transform=ax_main.get_yaxis_transform(),
                )
            if "resistance" in levels and levels["resistance"]:
                ax_main.annotate(
                    f"R: {levels['resistance']}", xy=(0.01, 0), xycoords='axes fraction',
                    xytext=(5, 0), textcoords='offset points',
                    color="#f44336", fontsize=8, fontweight="bold",
                    verticalalignment='top',
                    transform=ax_main.get_yaxis_transform(),
                )

        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                    facecolor=fig.get_facecolor(), edgecolor="none")
        matplotlib.pyplot.close(fig)

    except Exception as e:
        log.warning(f"Полный график {screen.symbol} не удался ({e}), рисую упрощённый")
        buf = io.BytesIO()
        fig, axes = mpf.plot(
            df,
            type="candle",
            style=_DARK_STYLE,
            title=title,
            volume=True,
            figsize=(12, 7),
            tight_layout=True,
            returnfig=True,
        )
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                    facecolor=fig.get_facecolor(), edgecolor="none")
        matplotlib.pyplot.close(fig)

    buf.seek(0)
    chart_bytes = buf.read()
    log.info(f"График {screen.symbol} сгенерирован ({len(chart_bytes)} bytes)")
    return chart_bytes
