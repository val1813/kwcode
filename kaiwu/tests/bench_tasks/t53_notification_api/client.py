"""
Notification system client SDK.

Bug: When sending a notification, the priority field is sent as a number (1-5)
instead of the string values "low"/"medium"/"high" that the API expects.
The API will reject numeric priority values with a 400 error.
"""

from typing import Optional

# Priority mapping used by the buggy client
_PRIORITY_MAP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}  # Bug: should map to "low"/"medium"/"high"


class NotificationClient:
    """Client SDK for the notification API."""

    def __init__(self):
        from api import handle_request
        self._request = handle_request

    def send_notification(self, user_id: str, title: str, body: str,
                          notif_type: str = "info", priority: int = 3,
                          _now: str = None) -> dict:
        """Send a notification. priority is 1 (low) to 5 (high).
        Bug: sends numeric priority instead of string "low"/"medium"/"high".
        """
        # Bug: should convert numeric priority to string:
        #   if priority <= 2: priority_str = "low"
        #   elif priority <= 4: priority_str = "medium"
        #   else: priority_str = "high"
        priority_str = priority  # Bug: sends the integer directly

        payload = {
            "user_id": user_id,
            "title": title,
            "body": body,
            "type": notif_type,
            "priority": priority_str,
        }
        if _now:
            payload["_now"] = _now
        resp = self._request("POST", "/notifications", body=payload)
        if resp["status"] != 201:
            raise RuntimeError(f"send_notification failed: {resp['body']}")
        return resp["body"]

    def get_notification(self, notif_id: int) -> Optional[dict]:
        resp = self._request("GET", f"/notifications/{notif_id}")
        if resp["status"] == 404:
            return None
        if resp["status"] != 200:
            raise RuntimeError(f"get_notification failed: {resp['body']}")
        return resp["body"]

    def list_notifications(self, user_id: str = None,
                           unread_only: bool = False) -> list[dict]:
        params = {}
        if user_id:
            params["user_id"] = user_id
        if unread_only:
            params["unread_only"] = "true"
        resp = self._request("GET", "/notifications", params=params)
        if resp["status"] != 200:
            raise RuntimeError(f"list_notifications failed: {resp['body']}")
        return resp["body"]["items"]

    def mark_read(self, ids: list[int], _now: str = None) -> int:
        """Mark notifications as read. Returns count of updated notifications."""
        body = {"ids": ids}  # Correct: sends "ids" key
        if _now:
            body["_now"] = _now
        resp = self._request("POST", "/notifications/mark-read", body=body)
        if resp["status"] != 200:
            raise RuntimeError(f"mark_read failed: {resp['body']}")
        return resp["body"]["marked_count"]
