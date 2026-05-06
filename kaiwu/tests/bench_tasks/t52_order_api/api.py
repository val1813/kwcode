"""
Order system API endpoints.

Bug: The create order endpoint expects items as a list of objects
[{"item_id": "x", "quantity": 2, "unit_price": 9.99}, ...], but the client
(per the API contract) sends items as a dict mapping item_id to quantity:
{"item-1": 2, "item-2": 1}. The API must accept the dict format and look up
prices from a catalog, not expect unit_price from the client.
"""

from datetime import datetime
from typing import Any
from models import (
    Order, OrderItem, OrderStatus,
    create_order, get_order, update_order_status, list_orders,
)

# Simple price catalog for tests
ITEM_CATALOG = {
    "item-1": 10.00,
    "item-2": 25.50,
    "item-3": 5.75,
    "item-4": 99.99,
}

VALID_STATUSES = {s.value for s in OrderStatus}


def handle_request(method: str, path: str, body: dict = None,
                   params: dict = None) -> dict:
    body = body or {}
    params = params or {}

    if method == "POST" and path == "/orders":
        return _create_order(body)
    if method == "GET" and path == "/orders":
        return _list_orders(params)
    if method == "GET" and path.startswith("/orders/"):
        order_id = int(path.split("/")[2])
        return _get_order(order_id)
    if method == "PATCH" and path.startswith("/orders/") and path.endswith("/status"):
        order_id = int(path.split("/")[2])
        return _update_status(order_id, body)
    return {"status": 404, "body": {"error": "not found"}}


def _create_order(body: dict) -> dict:
    customer_id = body.get("customer_id")
    if not customer_id:
        return {"status": 400, "body": {"error": "customer_id required"}}

    raw_items = body.get("items")
    if not raw_items:
        return {"status": 400, "body": {"error": "items required"}}

    # Bug: expects a list of dicts with unit_price included, but the client
    # sends a dict of {item_id: quantity}. Should be:
    #   if isinstance(raw_items, dict):
    #       items = [OrderItem(item_id=k, quantity=v,
    #                          unit_price=ITEM_CATALOG.get(k, 0.0))
    #                for k, v in raw_items.items()]
    if not isinstance(raw_items, list):
        return {"status": 400, "body": {"error": "items must be a list"}}

    items = []
    for entry in raw_items:
        if not isinstance(entry, dict) or "item_id" not in entry:
            return {"status": 400, "body": {"error": "each item needs item_id"}}
        items.append(OrderItem(
            item_id=entry["item_id"],
            quantity=entry.get("quantity", 1),
            unit_price=entry.get("unit_price", 0.0),
        ))

    now_str = body.get("_now")
    now = datetime.fromisoformat(now_str) if now_str else None
    order = create_order(customer_id, items, now=now)
    return {"status": 201, "body": order.to_dict()}


def _list_orders(params: dict) -> dict:
    customer_id = params.get("customer_id")
    orders = list_orders(customer_id=customer_id)
    return {"status": 200, "body": {"items": [o.to_dict() for o in orders]}}


def _get_order(order_id: int) -> dict:
    order = get_order(order_id)
    if order is None:
        return {"status": 404, "body": {"error": "order not found"}}
    return {"status": 200, "body": order.to_dict()}


def _update_status(order_id: int, body: dict) -> dict:
    status_str = body.get("status")
    if not status_str:
        return {"status": 400, "body": {"error": "status required"}}
    # Accept lowercase status values from client
    status_str_lower = status_str.lower()
    try:
        status = OrderStatus(status_str_lower)
    except ValueError:
        return {"status": 400, "body": {"error": f"invalid status: {status_str}"}}
    order = update_order_status(order_id, status)
    if order is None:
        return {"status": 404, "body": {"error": "order not found"}}
    return {"status": 200, "body": order.to_dict()}
