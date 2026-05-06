"""
Order system client SDK.

This client is correct — it implements the API contract as documented.
The bugs are in models.py (OrderStatus values) and api.py (items format).
"""

from typing import Optional


class OrderClient:
    """Client SDK for the order management API."""

    def __init__(self):
        from api import handle_request
        self._request = handle_request

    def create_order(self, customer_id: str, items: dict[str, int],
                     _now: str = None) -> dict:
        """Create an order. items is {item_id: quantity}."""
        body = {"customer_id": customer_id, "items": items}
        if _now:
            body["_now"] = _now
        resp = self._request("POST", "/orders", body=body)
        if resp["status"] != 201:
            raise RuntimeError(f"create_order failed: {resp['body']}")
        return resp["body"]

    def get_order(self, order_id: int) -> Optional[dict]:
        resp = self._request("GET", f"/orders/{order_id}")
        if resp["status"] == 404:
            return None
        if resp["status"] != 200:
            raise RuntimeError(f"get_order failed: {resp['body']}")
        return resp["body"]

    def list_orders(self, customer_id: str = None) -> list[dict]:
        params = {}
        if customer_id:
            params["customer_id"] = customer_id
        resp = self._request("GET", "/orders", params=params)
        if resp["status"] != 200:
            raise RuntimeError(f"list_orders failed: {resp['body']}")
        return resp["body"]["items"]

    def update_status(self, order_id: int, status: str) -> dict:
        """Update order status. status should be lowercase: pending/paid/shipped/cancelled."""
        resp = self._request("PATCH", f"/orders/{order_id}/status",
                             body={"status": status})
        if resp["status"] != 200:
            raise RuntimeError(f"update_status failed: {resp['body']}")
        return resp["body"]
