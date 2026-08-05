from __future__ import annotations
"""券商接口"""
from abc import ABC, abstractmethod
from trade.order_manager import Order, OrderStatus, OrderDirection
from config import COMMISSION_RATE, STAMP_TAX_RATE, MIN_COMMISSION


class BaseBroker(ABC):
    """券商抽象接口"""

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def get_balance(self) -> dict:
        """返回 {"total": float, "available": float, "frozen": float}"""
        pass

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """返回 [{"code": str, "quantity": int, "avg_price": float, "market_value": float}]"""
        pass

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """提交订单，返回 broker_order_id"""
        pass

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        pass

    @abstractmethod
    def query_order(self, broker_order_id: str) -> dict:
        """返回 {"status": str, "filled_price": float, "filled_quantity": int}"""
        pass


class SimulatedBroker(BaseBroker):
    """模拟券商（用于不接入真实券商的测试/模拟实盘）"""

    def __init__(self, initial_cash: float = 40000.0):
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._positions: dict[str, dict] = {}
        self._order_counter = 0
        self._orders: dict[str, dict] = {}
        self._connected = False

    def connect(self):
        self._connected = True
        print(f"模拟券商已连接，初始资金: {self._cash:,.2f}")

    def disconnect(self):
        self._connected = False
        print("模拟券商已断开")

    def get_balance(self) -> dict:
        frozen = sum(
            pos["quantity"] * pos["avg_price"]
            for pos in self._positions.values()
        )
        return {
            "total": self._cash + frozen,
            "available": self._cash,
            "frozen": frozen,
        }

    def get_positions(self) -> list[dict]:
        return [
            {"code": code, **pos}
            for code, pos in self._positions.items()
            if pos["quantity"] > 0
        ]

    def place_order(self, order: Order) -> str:
        self._order_counter += 1
        broker_id = f"SIM-{self._order_counter:06d}"

        result = self._simulate_fill(order)
        self._orders[broker_id] = result

        return broker_id

    def _simulate_fill(self, order: Order) -> dict:
        """立即模拟成交"""
        price = order.price
        quantity = order.quantity
        turnover = price * quantity

        commission = max(turnover * COMMISSION_RATE, MIN_COMMISSION)
        stamp_tax = 0.0

        if order.direction == OrderDirection.BUY:
            total_cost = turnover + commission
            if total_cost > self._cash:
                return {"status": "rejected", "filled_price": 0, "filled_quantity": 0}

            self._cash -= total_cost
            if order.code in self._positions:
                pos = self._positions[order.code]
                total_qty = pos["quantity"] + quantity
                pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + price * quantity) / total_qty
                pos["quantity"] = total_qty
            else:
                self._positions[order.code] = {
                    "quantity": quantity,
                    "avg_price": price,
                    "market_value": turnover,
                }

        else:
            pos = self._positions.get(order.code)
            if not pos or pos["quantity"] < quantity:
                return {"status": "rejected", "filled_price": 0, "filled_quantity": 0}

            stamp_tax = 0.0 if order.code.startswith("5") else turnover * STAMP_TAX_RATE
            self._cash += turnover - commission - stamp_tax
            pos["quantity"] -= quantity
            if pos["quantity"] == 0:
                del self._positions[order.code]

        return {"status": "filled", "filled_price": price, "filled_quantity": quantity}

    def cancel_order(self, broker_order_id: str) -> bool:
        return False

    def query_order(self, broker_order_id: str) -> dict:
        return self._orders.get(broker_order_id, {"status": "unknown"})


class VNPYBroker(BaseBroker):
    """VNPY 券商接口（预留）"""

    def connect(self):
        raise NotImplementedError("VNPY 对接待实现")

    def disconnect(self):
        raise NotImplementedError("VNPY 对接待实现")

    def get_balance(self) -> dict:
        raise NotImplementedError("VNPY 对接待实现")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("VNPY 对接待实现")

    def place_order(self, order: Order) -> str:
        raise NotImplementedError("VNPY 对接待实现")

    def cancel_order(self, broker_order_id: str) -> bool:
        raise NotImplementedError("VNPY 对接待实现")

    def query_order(self, broker_order_id: str) -> dict:
        raise NotImplementedError("VNPY 对接待实现")
