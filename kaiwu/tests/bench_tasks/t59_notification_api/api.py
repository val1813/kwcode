"""
Notification system API endpoints.

Bug: The bulk mark-read endpoint parses the request body looking for the key
"notification_ids", but the API contract (and client) sends the key "ids".
So bulk mark-read always receives an empty list and marks nothing as read.
"""

from datetime import datetime
from models import (
    NotificationType, Priority,
    create_notification, get_notification,
    list_notifications, mark_read,
)


def handle_request(method: str, path: str, body: dict = None,
                   params: dict = None) -> dict:
    body = body or {}
    params = params or {}

    if method == "POST" and path == "/notifications":
        return _create_notification(body)
    if method == "GET" and path == "/notifications":
        return _list_notifications(params)
    if method == "GET" and path.startswith("/notifications/"):
        notif_id = int(path.split("/")[2])
        return _get_notification(notif_id)
    if method == "POST" and path == "/notifications/mark-read":
        return _mark_read(body)
    return {"status": 404, "body": {"error": "not found"}}


def _create_notification(body: dict) -> dict:
    required = ("user_id", "title", "body", "type", "priority")
    for field in required:
        if field not in body:
            return {"status": 400, "body": {"error": f"{field} required"}}

    try:
        notif_type = NotificationType(body["type"])
    except ValueError:
        return {"status": 400, "body": {"error": f"invalid type: {body['type']}"}}

    try:
        priority = Priority(body["priority"])
    except ValueError:
        return {"status": 400, "body": {"error": f"invalid priority: {body['priority']}"}}

    now_str = body.get("_now")
    now = datetime.fromisoformat(now_str) if now_str else None

    n = create_notification(
        user_id=body["user_id"],
        title=body["title"],
        body=body["body"],
        type=notif_type,
        priority=priority,
        now=now,
    )
    return {"status": 201, "body": n.to_dict()}


def _list_notifications(params: dict) -> dict:
    user_id = params.get("user_id")
    unread_only = params.get("unread_only", "false").lower() == "true"
    items = list_notifications(user_id=user_id, unread_only=unread_only)
    return {"status": 200, "body": {"items": [n.to_dict() for n in items]}}


def _get_notification(notif_id: int) -> dict:
    n = get_notification(notif_id)
    if n is None:
        return {"status": 404, "body": {"error": "notification not found"}}
    return {"status": 200, "body": n.to_dict()}


def _mark_read(body: dict) -> dict:
    # Bug: reads "notification_ids" but client sends "ids"
    ids = body.get("notification_ids", [])
    if not isinstance(ids, list):
        return {"status": 400, "body": {"error": "ids must be a list"}}

    now_str = body.get("_now")
    now = datetime.fromisoformat(now_str) if now_str else None

    count = mark_read(ids, now=now)
    return {"status": 200, "body": {"marked_count": count}}
