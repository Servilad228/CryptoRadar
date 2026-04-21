"""
CryptoRadar — Трекер позиций.
Battle Mode: WebSocket (реалтайм).
Demo Mode: REST проверка каждые 10 минут.
Auto-breakeven при 40% пути к TP.
"""

import json
import threading
import time
from typing import Optional

from pybit.unified_trading import HTTP, WebSocket

import config
import database
import scanner
import lesson_analyzer
from logger import log


class PositionTracker:
    """
    Мониторинг открытых ордеров + auto-breakeven.
    """

    def __init__(self, mode: str = None):
        self.mode = mode or config.TRADING_MODE
        self._ws: Optional[WebSocket] = None
        self._running = False
        self._lock = threading.Lock()  # защита от конфликта со сканом

    def start(self):
        """Запуск трекера."""
        if self.mode == "battle" and config.BYBIT_API_KEY:
            self._start_websocket()
        self._running = True
        log.info(f"PositionTracker запущен (mode={self.mode})")

    def stop(self):
        """Остановка трекера."""
        self._running = False
        if self._ws:
            try:
                self._ws.exit()
            except Exception:
                pass
        log.info("PositionTracker остановлен")

    def _start_websocket(self):
        """Подписка на position stream через pybit WebSocket."""
        try:
            self._ws = WebSocket(
                testnet=False,
                channel_type="private",
                api_key=config.BYBIT_API_KEY,
                api_secret=config.BYBIT_API_SECRET,
            )
            self._ws.position_stream(callback=self._on_position_update)
            log.info("WebSocket: подписка на position stream")
        except Exception as e:
            log.error(f"WebSocket: ошибка подключения: {e}")
            self._ws = None

    def _on_position_update(self, message):
        """Callback от WebSocket при изменении позиции."""
        try:
            data = message.get("data", [])
            for pos in data:
                symbol = pos.get("symbol", "")
                size = float(pos.get("size", 0))

                if size == 0:
                    # Позиция закрыта — ищем в open_orders
                    self._handle_closed_position(symbol, pos)
                else:
                    # Позиция открыта — проверяем breakeven
                    self._check_breakeven_for_position(symbol, pos)

        except Exception as e:
            log.error(f"WebSocket position callback error: {e}")

    def check_positions_rest(self):
        """
        REST проверка позиций (для Demo Mode).
        Вызывается из APScheduler каждые 10 минут.
        """
        if not self._running:
            return

        with self._lock:
            orders = database.get_open_orders()
            if not orders:
                return

            log.debug(f"PositionTracker: проверяю {len(orders)} открытых ордеров (REST)")

            try:
                session = self._create_session()
            except Exception as e:
                log.error(f"PositionTracker: ошибка создания сессии: {e}")
                return

            for order in orders:
                try:
                    self._check_order_status(session, order)
                except Exception as e:
                    log.error(f"PositionTracker: ошибка проверки {order['symbol']}: {e}")
                    continue

    def _create_session(self) -> HTTP:
        """Создаёт сессию Bybit."""
        if self.mode == "demo":
            return HTTP(
                api_key=config.BYBIT_API_KEY,
                api_secret=config.BYBIT_API_SECRET,
                demo=True,
            )
        return HTTP(
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET,
        )

    def _check_order_status(self, session: HTTP, order: dict):
        """Проверяет статус одного ордера."""
        symbol = order["symbol"]
        bybit_id = order.get("bybit_order_id", "")

        if not bybit_id:
            return

        # Проверяем позицию
        try:
            result = session.get_positions(
                category="linear", symbol=symbol
            )
            positions = result["result"]["list"]
        except Exception as e:
            log.debug(f"Не удалось получить позицию {symbol}: {e}")
            return

        # Ищем активную позицию
        has_position = False
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                has_position = True
                self._check_breakeven_for_position(symbol, pos, order=order)
                break

        if not has_position:
            # Позиция закрыта — определяем причину
            self._resolve_closed_order(session, order)

    def _check_breakeven_for_position(self, symbol: str, pos: dict, order: dict = None):
        """Проверяет нужно ли перенести SL в breakeven."""
        if order is None:
            orders = database.get_open_orders()
            order = next((o for o in orders if o["symbol"] == symbol), None)
            if not order:
                return

        if order.get("sl_moved_to_be"):
            return  # уже перенесён

        entry = order["entry"]
        tp = order["tp"]
        direction = order["direction"]

        # Текущая цена
        mark_price = float(pos.get("markPrice", 0))
        if mark_price == 0:
            return

        # Считаем % пути к TP
        total_distance = abs(tp - entry)
        if total_distance == 0:
            return

        if direction == "LONG":
            current_progress = mark_price - entry
        else:
            current_progress = entry - mark_price

        progress_pct = (current_progress / total_distance) * 100

        if progress_pct >= config.BREAKEVEN_TRIGGER_PCT:
            # Переносим SL в breakeven
            try:
                session = self._create_session()
                session.set_trading_stop(
                    category="linear",
                    symbol=symbol,
                    stopLoss=str(entry),
                    tpslMode="Full",
                    positionIdx=0,
                )
                database.update_open_order_sl(order["id"], entry, True)
                log.info(
                    f"Auto-breakeven: {symbol} SL → {entry} "
                    f"(прогресс: {progress_pct:.0f}%)"
                )

                # Уведомление в Telegram
                import telegram_bot
                telegram_bot.send_message(
                    f"🛡️ Auto-Breakeven\n\n"
                    f"{symbol} {direction}\n"
                    f"SL перенесён в безубыток: {entry}\n"
                    f"Прогресс к TP: {progress_pct:.0f}%"
                )

            except Exception as e:
                log.error(f"Auto-breakeven error {symbol}: {e}")

    def _resolve_closed_order(self, session: HTTP, order: dict):
        """Определяет причину закрытия и обрабатывает."""
        symbol = order["symbol"]
        entry = order["entry"]
        sl = order["sl"]
        tp = order["tp"]
        direction = order["direction"]

        # Получаем последнюю цену для определения причины
        try:
            tickers = session.get_tickers(
                category="linear", symbol=symbol
            )
            last_price = float(tickers["result"]["list"][0]["lastPrice"])
        except Exception:
            last_price = 0

        # Определяем причину закрытия
        if direction == "LONG":
            if last_price >= tp * 0.999:
                close_reason = "tp_hit"
                close_price = tp
            elif last_price <= sl * 1.001:
                close_reason = "sl_hit"
                close_price = sl
            else:
                close_reason = "manual"
                close_price = last_price
        else:
            if last_price <= tp * 1.001:
                close_reason = "tp_hit"
                close_price = tp
            elif last_price >= sl * 0.999:
                close_reason = "sl_hit"
                close_price = sl
            else:
                close_reason = "manual"
                close_price = last_price

        # Рассчитываем PnL
        qty = order["qty"]
        if direction == "LONG":
            pnl = (close_price - entry) * qty
        else:
            pnl = (entry - close_price) * qty

        # Закрываем ордер в БД
        database.close_order(
            order_id=order["id"],
            symbol=symbol,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            qty=qty,
            rr_ratio=order["rr_ratio"],
            mode=order["mode"],
            bybit_order_id=order.get("bybit_order_id", ""),
            close_price=close_price,
            close_reason=close_reason,
            pnl=pnl,
            created_at=order["created_at"],
        )

        # Уведомление в Telegram
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        pnl_sign = "+" if pnl >= 0 else ""
        import telegram_bot
        telegram_bot.send_message(
            f"{pnl_emoji} Ордер закрыт\n\n"
            f"{symbol} {direction} ({close_reason})\n"
            f"PnL: {pnl_sign}${pnl:.2f}\n"
            f"Режим: {'Demo' if order['mode'] == 'demo' else 'Battle'}"
        )

        # Генерируем урок
        self._generate_lesson(order, close_reason, close_price, pnl)

    def _generate_lesson(self, order: dict, close_reason: str, 
                          close_price: float, pnl: float):
        """Запускает REVIEW_MODEL для анализа сделки."""
        try:
            symbol = order["symbol"]

            # Загружаем свечи за период сделки
            klines = scanner.get_klines(symbol, interval="60", limit=100)
            candles = []
            if klines is not None and not klines.empty:
                for idx, row in klines.iterrows():
                    candles.append({
                        "time": str(idx),
                        "o": round(row["open"], 6),
                        "h": round(row["high"], 6),
                        "l": round(row["low"], 6),
                        "c": round(row["close"], 6),
                        "v": round(row["volume"], 2),
                    })
            candles_json = json.dumps(candles, ensure_ascii=False)

            # Добавляем данные закрытия в order_data
            order_data = dict(order)
            order_data["close_price"] = close_price
            order_data["pnl"] = pnl
            order_data["close_reason"] = close_reason

            # Запускаем анализ
            analysis, tip_analyzer, tip_order = lesson_analyzer.analyze_trade(
                order_data, candles_json, close_reason
            )

            # Генерируем график для снимка
            chart_bytes = None
            try:
                from models import ScreenResult, Direction
                import chart
                screen = ScreenResult(
                    symbol=symbol,
                    last_price=close_price,
                    direction=Direction.LONG if order["direction"] == "LONG" else Direction.SHORT,
                    score_15m=0, score_1h=0,
                )
                chart_bytes = chart.generate(screen, klines)
            except Exception as e:
                log.warning(f"Не удалось сгенерировать график для урока: {e}")

            # Сохраняем
            lesson_analyzer.save_lesson_with_tips(
                order_data=order_data,
                analysis=analysis,
                tip_analyzer=tip_analyzer,
                tip_order=tip_order,
                chart_bytes=chart_bytes,
            )

        except Exception as e:
            log.error(f"Ошибка генерации урока для {order['symbol']}: {e}")

    def _handle_closed_position(self, symbol: str, pos: dict):
        """Обработка закрытия позиции через WebSocket."""
        orders = database.get_open_orders()
        order = next((o for o in orders if o["symbol"] == symbol), None)
        if order:
            session = self._create_session()
            self._resolve_closed_order(session, order)


# Глобальный экземпляр
position_tracker = PositionTracker()
