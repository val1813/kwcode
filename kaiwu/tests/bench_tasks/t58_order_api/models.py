"""
Order system data models.

Bug: OrderStatus enum values use uppercase strings (PENDING, PAID, SHIPPED,
CANCELLED) but the API is supposed to return lowercase values (pending, paid,
shipped, cancelled) to match the documented contract and what the client expects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(Enum):
    # Bug: values should be lowercase "pending", "paid", "shipped", "cancelled"
    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderItem:
    item_id: str
    quantity: int
    unit_price: float

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
        }


@dataclass
class Order:
    id: int
    customer_id: str
    items: list[OrderItem]
    status: OrderStatus
    created_at: datetime
    total: float = 0.0
    notes: Optional[str] = None

    def __post_init__(self):
        if self.total == 0.0 and self.items:
            self.total = sum(i.quantity * i.unit_price for i in self.items)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "items": [i.to_dict() for i in self.items],
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "total": self.total,
            "notes": self.notes,
        }


# In-memory store
_orders: dict[int, Order] = {}
_next_id: int = 1


def reset_store() -> None:
    global _next_id
    _orders.clear()
    _next_id = 1


def create_order(customer_id: str, items: list[OrderItem],
                 now: datetime = None) -> Order:
    global _next_id
    if now is None:
        now = datetime.utcnow()
    order = Order(
        id=_next_id,
        customer_id=customer_id,
        items=items,
        status=OrderStatus.PENDING,
        created_at=now,
    )
    _orders[_next_id] = order
    _next_id += 1
    return order


def get_order(order_id: int) -> Optional[Order]:
    return _orders.get(order_id)


def update_order_status(order_id: int, status: OrderStatus) -> Optional[Order]:
    order = _orders.get(order_id)
    if order is None:
        return None
    order.status = status
    return order


def list_orders(customer_id: str = None) -> list[Order]:
    orders = list(_orders.values())
    if customer_id:
        orders = [o for o in orders if o.customer_id == customer_id]
    return orders
