from __future__ import annotations
"""订单管理"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderDirection(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    order_id: str
    code: str
    direction: OrderDirection
    price: float
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_quantity: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    broker_order_id: str = ""


class OrderManager:
    def __init__(self):
        self._orders: dict[str, Order] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"ORD-{self._counter:06d}"

    def create_order(
        self,
        code: str,
        direction: OrderDirection,
        price: float,
        quantity: int,
        order_type: OrderType = OrderType.LIMIT,
    ) -> Order:
        order_id = self._next_id()
        order = Order(
            order_id=order_id,
            code=code,
            direction=direction,
            price=price,
            quantity=quantity,
            order_type=order_type,
        )
        self._orders[order_id] = order
        return order

    def submit_order(self, order_id: str, broker_order_id: str):
        order = self._orders[order_id]
        order.status = OrderStatus.SUBMITTED
        order.broker_order_id = broker_order_id
        order.updated_at = datetime.now()

    def fill_order(self, order_id: str, filled_price: float, filled_quantity: int = 0):
        order = self._orders[order_id]
        order.filled_price = filled_price
        order.filled_quantity = filled_quantity or order.quantity
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
        order.updated_at = datetime.now()

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order:
            return False
        if order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            return True
        return False

    def reject_order(self, order_id: str):
        order = self._orders[order_id]
        order.status = OrderStatus.REJECTED
        order.updated_at = datetime.now()

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_pending_orders(self) -> list[Order]:
        return [
            o for o in self._orders.values()
            if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED)
        ]

    def get_filled_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == OrderStatus.FILLED]

    def get_all_orders(self) -> list[Order]:
        return list(self._orders.values())
