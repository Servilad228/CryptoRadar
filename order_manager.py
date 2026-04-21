"""
CryptoRadar — Менеджер ордеров.
Размещение на Bybit (Demo / Battle), CRUD через SQLite.
"""

from typing import Optional

from pybit.unified_trading import HTTP

import config
import database
from logger import log
from models import OrderParams, Order, Direction


class OrderManager:
    """Управление ордерами на Bybit (Demo или Battle)."""

    def __init__(self, mode: str = None):
        self.mode = mode or config.TRADING_MODE
        self._session: Optional[HTTP] = None

    def _get_session(self) -> HTTP:
        """Создаёт или возвращает Bybit HTTP сессию."""
        if self._session is None:
            if self.mode == "demo":
                self._session = HTTP(
                    api_key=config.BYBIT_API_KEY,
                    api_secret=config.BYBIT_API_SECRET,
                    demo=True,
                )
                log.info("OrderManager: подключён к Bybit Demo API")
            else:
                self._session = HTTP(
                    api_key=config.BYBIT_API_KEY,
                    api_secret=config.BYBIT_API_SECRET,
                )
                log.info("OrderManager: подключён к Bybit Production API")
        return self._session

    def switch_mode(self, new_mode: str):
        """Переключает режим, сбрасывает сессию."""
        self.mode = new_mode
        self._session = None
        config.TRADING_MODE = new_mode
        log.info(f"OrderManager: режим переключён → {new_mode}")

    def validate_api_keys(self) -> tuple[bool, str]:
        """Проверяет валидность Bybit API ключей."""
        if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
            return False, "BYBIT_API_KEY и BYBIT_API_SECRET не установлены"

        try:
            session = self._get_session()
            result = session.get_wallet_balance(
                accountType="UNIFIED", coin="USDT"
            )
            if result["retCode"] == 0:
                return True, "API ключи валидны"
            return False, f"Bybit error: {result['retMsg']}"
        except Exception as e:
            self._session = None
            return False, f"Ошибка подключения: {e}"

    def get_instrument_info(self, symbol: str) -> dict:
        """Получает информацию об инструменте (minOrderQty, qtyStep)."""
        session = self._get_session()
        result = session.get_instruments_info(
            category="linear", symbol=symbol
        )
        instruments = result["result"]["list"]
        if not instruments:
            raise ValueError(f"Инструмент {symbol} не найден")
        return instruments[0]

    def _round_qty(self, qty: float, symbol: str) -> float:
        """Округляет количество до qtyStep инструмента."""
        info = self.get_instrument_info(symbol)
        lot_filter = info.get("lotSizeFilter", {})
        qty_step = float(lot_filter.get("qtyStep", "0.001"))
        min_qty = float(lot_filter.get("minOrderQty", "0.001"))

        # Округляем вниз до кратного qtyStep
        rounded = round(qty - (qty % qty_step), 10)
        if rounded < min_qty:
            rounded = min_qty
        return rounded

    def place_order(self, params: OrderParams) -> Optional[dict]:
        """
        Размещает ордер на Bybit (demo или battle).
        Возвращает dict с данными ордера или None при ошибке.
        """
        try:
            session = self._get_session()
            symbol = params.symbol
            side = "Buy" if params.direction == Direction.LONG else "Sell"

            # Округляем qty
            qty = self._round_qty(params.qty, symbol)
            qty_str = str(qty)

            # Размещаем лимитный ордер с TP/SL
            result = session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Limit",
                qty=qty_str,
                price=str(params.entry),
                stopLoss=str(params.sl),
                takeProfit=str(params.tp),
                tpslMode="Full",
                timeInForce="GTC",
            )

            if result["retCode"] != 0:
                log.error(f"Bybit place_order error: {result['retMsg']}")
                return None

            bybit_order_id = result["result"]["orderId"]
            order_id = database.generate_id()

            # Сохраняем в SQLite
            database.add_open_order(
                order_id=order_id,
                symbol=symbol,
                direction=params.direction.value,
                entry=params.entry,
                sl=params.sl,
                tp=params.tp,
                qty=qty,
                rr_ratio=params.rr_ratio,
                mode=self.mode,
                bybit_order_id=bybit_order_id,
                ai_reasoning=params.reasoning,
            )

            log.info(
                f"Ордер размещён: {symbol} {side} | "
                f"Entry={params.entry} SL={params.sl} TP={params.tp} | "
                f"Mode={self.mode} | Bybit ID={bybit_order_id}"
            )

            return {
                "order_id": order_id,
                "bybit_order_id": bybit_order_id,
                "symbol": symbol,
                "direction": params.direction.value,
                "entry": params.entry,
                "sl": params.sl,
                "tp": params.tp,
                "qty": qty,
                "rr_ratio": params.rr_ratio,
                "mode": self.mode,
            }

        except Exception as e:
            log.error(f"Ошибка размещения ордера {params.symbol}: {e}")
            return None

    def get_open_orders(self) -> list[dict]:
        """Возвращает открытые ордера из SQLite."""
        return database.get_open_orders()

    def cancel_order(self, order_id: str) -> tuple[bool, str]:
        """Отменяет ордер на Bybit и удаляет из SQLite."""
        order = database.get_open_order(order_id)
        if not order:
            return False, "Ордер не найден"

        try:
            if order["bybit_order_id"]:
                session = self._get_session()
                result = session.cancel_order(
                    category="linear",
                    symbol=order["symbol"],
                    orderId=order["bybit_order_id"],
                )
                if result["retCode"] != 0:
                    log.warning(f"Bybit cancel warning: {result['retMsg']}")

            database.remove_open_order(order_id)
            log.info(f"Ордер {order_id[:8]} отменён")
            return True, "Ордер отменён"

        except Exception as e:
            log.error(f"Ошибка отмены ордера {order_id[:8]}: {e}")
            return False, str(e)

    def move_sl_to_breakeven(self, order_id: str) -> tuple[bool, str]:
        """Переносит SL на уровень входа (breakeven)."""
        order = database.get_open_order(order_id)
        if not order:
            return False, "Ордер не найден"

        if order["sl_moved_to_be"]:
            return True, "SL уже в безубытке"

        try:
            if order["bybit_order_id"]:
                session = self._get_session()
                session.set_trading_stop(
                    category="linear",
                    symbol=order["symbol"],
                    stopLoss=str(order["entry"]),
                    tpslMode="Full",
                    positionIdx=0,
                )

            database.update_open_order_sl(order_id, order["entry"], True)
            log.info(f"Ордер {order_id[:8]}: SL → breakeven ({order['entry']})")
            return True, f"SL перенесён в безубыток: {order['entry']}"

        except Exception as e:
            log.error(f"Ошибка breakeven {order_id[:8]}: {e}")
            return False, str(e)

    def get_position_info(self, symbol: str) -> Optional[dict]:
        """Получает информацию о текущей позиции."""
        try:
            session = self._get_session()
            result = session.get_positions(
                category="linear", symbol=symbol
            )
            positions = result["result"]["list"]
            for pos in positions:
                if float(pos.get("size", 0)) > 0:
                    return pos
            return None
        except Exception as e:
            log.error(f"Ошибка получения позиции {symbol}: {e}")
            return None


# Глобальный экземпляр
order_manager = OrderManager()
