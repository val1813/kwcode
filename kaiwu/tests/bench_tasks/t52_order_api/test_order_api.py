"""Tests for order system API interface consistency (t58)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import models
from client import OrderClient

FIXED_NOW = "2024-03-10T09:00:00"


@pytest.fixture(autouse=True)
def reset():
    models.reset_store()
    yield
    models.reset_store()


class TestOrderStatusValues:
    def test_status_pending_is_lowercase(self):
        """OrderStatus.PENDING.value must be 'pending', not 'PENDING'."""
        assert models.OrderStatus.PENDING.value == "pending", (
            f"Expected 'pending', got '{models.OrderStatus.PENDING.value}'"
        )

    def test_status_paid_is_lowercase(self):
        assert models.OrderStatus.PAID.value == "paid"

    def test_status_shipped_is_lowercase(self):
        assert models.OrderStatus.SHIPPED.value == "shipped"

    def test_status_cancelled_is_lowercase(self):
        assert models.OrderStatus.CANCELLED.value == "cancelled"

    def test_order_to_dict_status_lowercase(self):
        """Order.to_dict() must return lowercase status string."""
        client = OrderClient()
        order = client.create_order("cust-1", {"item-1": 2}, _now=FIXED_NOW)
        assert order["status"] == "pending", (
            f"Expected 'pending', got '{order['status']}'"
        )


class TestCreateOrderItemsFormat:
    def test_create_with_dict_items(self):
        """API must accept items as {item_id: quantity} dict."""
        client = OrderClient()
        order = client.create_order(
            "cust-1",
            {"item-1": 2, "item-2": 1},
            _now=FIXED_NOW,
        )
        assert order["id"] is not None
        assert order["customer_id"] == "cust-1"

    def test_create_items_count(self):
        client = OrderClient()
        order = client.create_order("cust-1", {"item-1": 3, "item-3": 1}, _now=FIXED_NOW)
        assert len(order["items"]) == 2

    def test_create_items_quantities(self):
        client = OrderClient()
        order = client.create_order("cust-1", {"item-2": 5}, _now=FIXED_NOW)
        item = order["items"][0]
        assert item["item_id"] == "item-2"
        assert item["quantity"] == 5

    def test_create_total_calculated_from_catalog(self):
        """Total must be calculated from the server-side catalog, not client-provided prices."""
        client = OrderClient()
        # item-1 costs 10.00, qty 2 => 20.00; item-3 costs 5.75, qty 4 => 23.00; total = 43.00
        order = client.create_order("cust-1", {"item-1": 2, "item-3": 4}, _now=FIXED_NOW)
        assert abs(order["total"] - 43.00) < 0.01


class TestOrderStatusUpdate:
    def test_update_to_paid(self):
        client = OrderClient()
        order = client.create_order("cust-1", {"item-1": 1}, _now=FIXED_NOW)
        updated = client.update_status(order["id"], "paid")
        assert updated["status"] == "paid"

    def test_update_to_shipped(self):
        client = OrderClient()
        order = client.create_order("cust-1", {"item-1": 1}, _now=FIXED_NOW)
        client.update_status(order["id"], "paid")
        updated = client.update_status(order["id"], "shipped")
        assert updated["status"] == "shipped"

    def test_update_to_cancelled(self):
        client = OrderClient()
        order = client.create_order("cust-1", {"item-1": 1}, _now=FIXED_NOW)
        updated = client.update_status(order["id"], "cancelled")
        assert updated["status"] == "cancelled"


class TestListOrders:
    def test_list_all_orders(self):
        client = OrderClient()
        client.create_order("cust-1", {"item-1": 1}, _now=FIXED_NOW)
        client.create_order("cust-2", {"item-2": 2}, _now=FIXED_NOW)
        orders = client.list_orders()
        assert len(orders) == 2

    def test_list_by_customer(self):
        client = OrderClient()
        client.create_order("cust-1", {"item-1": 1}, _now=FIXED_NOW)
        client.create_order("cust-1", {"item-2": 1}, _now=FIXED_NOW)
        client.create_order("cust-2", {"item-3": 1}, _now=FIXED_NOW)
        orders = client.list_orders(customer_id="cust-1")
        assert len(orders) == 2
        assert all(o["customer_id"] == "cust-1" for o in orders)

    def test_get_order_roundtrip(self):
        client = OrderClient()
        created = client.create_order("cust-99", {"item-4": 1}, _now=FIXED_NOW)
        fetched = client.get_order(created["id"])
        assert fetched["id"] == created["id"]
        assert fetched["status"] == "pending"
