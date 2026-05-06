"""
Notification system data models.

This file is correct — no bugs here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class NotificationType(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Notification:
    id: int
    user_id: str
    title: str
    body: str
    type: NotificationType
    priority: Priority
    created_at: datetime
    read: bool = False
    read_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "body": self.body,
            "type": self.type.value,
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "read": self.read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


# In-memory store
_notifications: dict[int, Notification] = {}
_next_id: int = 1


def reset_store() -> None:
    global _next_id
    _notifications.clear()
    _next_id = 1


def create_notification(user_id: str, title: str, body: str,
                         type: NotificationType, priority: Priority,
                         now: datetime = None) -> Notification:
    global _next_id
    if now is None:
        now = datetime.utcnow()
    n = Notification(
        id=_next_id,
        user_id=user_id,
        title=title,
        body=body,
        type=type,
        priority=priority,
        created_at=now,
    )
    _notifications[_next_id] = n
    _next_id += 1
    return n


def get_notification(notif_id: int) -> Optional[Notification]:
    return _notifications.get(notif_id)


def list_notifications(user_id: str = None, unread_only: bool = False) -> list[Notification]:
    items = list(_notifications.values())
    if user_id:
        items = [n for n in items if n.user_id == user_id]
    if unread_only:
        items = [n for n in items if not n.read]
    return items


def mark_read(notif_ids: list[int], now: datetime = None) -> int:
    """Mark notifications as read. Returns count of notifications actually updated."""
    if now is None:
        now = datetime.utcnow()
    count = 0
    for nid in notif_ids:
        n = _notifications.get(nid)
        if n and not n.read:
            n.read = True
            n.read_at = now
            count += 1
    return count
