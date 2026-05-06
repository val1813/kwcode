"""Tests for notification system API interface consistency (t59)."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import models
from client import NotificationClient

FIXED_NOW = "2024-02-20T14:00:00"
FIXED_NOW2 = "2024-02-20T15:00:00"


@pytest.fixture(autouse=True)
def reset():
    models.reset_store()
    yield
    models.reset_store()


class TestSendNotificationPriority:
    def test_send_low_priority(self):
        """Client must send priority as string 'low', not integer."""
        client = NotificationClient()
        n = client.send_notification(
            "user-1", "Hello", "World", priority=1, _now=FIXED_NOW
        )
        assert n["priority"] == "low", (
            f"Expected 'low', got '{n['priority']}'"
        )

    def test_send_medium_priority(self):
        client = NotificationClient()
        n = client.send_notification(
            "user-1", "Hello", "World", priority=3, _now=FIXED_NOW
        )
        assert n["priority"] == "medium", (
            f"Expected 'medium', got '{n['priority']}'"
        )

    def test_send_high_priority(self):
        client = NotificationClient()
        n = client.send_notification(
            "user-1", "Alert", "Critical", priority=5, _now=FIXED_NOW
        )
        assert n["priority"] == "high", (
            f"Expected 'high', got '{n['priority']}'"
        )

    def test_send_priority_2_is_low(self):
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", priority=2, _now=FIXED_NOW)
        assert n["priority"] == "low"

    def test_send_priority_4_is_medium(self):
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", priority=4, _now=FIXED_NOW)
        assert n["priority"] == "medium"


class TestMarkRead:
    def test_mark_read_returns_correct_count(self):
        """mark_read must use 'ids' key; API bug uses 'notification_ids'."""
        client = NotificationClient()
        n1 = client.send_notification("user-1", "T1", "B1", _now=FIXED_NOW)
        n2 = client.send_notification("user-1", "T2", "B2", _now=FIXED_NOW)
        count = client.mark_read([n1["id"], n2["id"]], _now=FIXED_NOW2)
        assert count == 2, f"Expected 2 marked, got {count}"

    def test_mark_read_single(self):
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", _now=FIXED_NOW)
        count = client.mark_read([n["id"]], _now=FIXED_NOW2)
        assert count == 1

    def test_mark_read_updates_read_flag(self):
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", _now=FIXED_NOW)
        client.mark_read([n["id"]], _now=FIXED_NOW2)
        fetched = client.get_notification(n["id"])
        assert fetched["read"] is True

    def test_mark_read_sets_read_at(self):
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", _now=FIXED_NOW)
        client.mark_read([n["id"]], _now=FIXED_NOW2)
        fetched = client.get_notification(n["id"])
        assert fetched["read_at"] == FIXED_NOW2

    def test_mark_read_idempotent(self):
        """Marking already-read notifications should not double-count."""
        client = NotificationClient()
        n = client.send_notification("user-1", "T", "B", _now=FIXED_NOW)
        client.mark_read([n["id"]], _now=FIXED_NOW2)
        count2 = client.mark_read([n["id"]], _now=FIXED_NOW2)
        assert count2 == 0


class TestListNotifications:
    def test_list_unread_only(self):
        client = NotificationClient()
        n1 = client.send_notification("user-1", "T1", "B1", _now=FIXED_NOW)
        n2 = client.send_notification("user-1", "T2", "B2", _now=FIXED_NOW)
        client.mark_read([n1["id"]], _now=FIXED_NOW2)
        unread = client.list_notifications(user_id="user-1", unread_only=True)
        assert len(unread) == 1
        assert unread[0]["id"] == n2["id"]

    def test_list_by_user(self):
        client = NotificationClient()
        client.send_notification("user-1", "T1", "B1", _now=FIXED_NOW)
        client.send_notification("user-2", "T2", "B2", _now=FIXED_NOW)
        items = client.list_notifications(user_id="user-1")
        assert len(items) == 1
        assert items[0]["user_id"] == "user-1"
