"""
User management client SDK.

This client is correct — it implements the API contract as documented.
The bugs are in models.py and api.py.
"""

from typing import Optional, Any


class UserClient:
    """Client SDK for the user management API."""

    def __init__(self):
        # Import here to allow api.py bugs to surface at call time
        from api import handle_request
        self._request = handle_request

    def create_user(self, username: str, email: str, role: str = "viewer",
                    display_name: str = None, _now: str = None) -> dict:
        body = {"username": username, "email": email, "role": role}
        if display_name:
            body["display_name"] = display_name
        if _now:
            body["_now"] = _now
        resp = self._request("POST", "/users", body=body)
        if resp["status"] != 201:
            raise RuntimeError(f"create_user failed: {resp['body']}")
        return resp["body"]

    def get_user(self, user_id: int) -> Optional[dict]:
        resp = self._request("GET", f"/users/{user_id}")
        if resp["status"] == 404:
            return None
        if resp["status"] != 200:
            raise RuntimeError(f"get_user failed: {resp['body']}")
        return resp["body"]

    def list_users(self, offset: int = 0, limit: int = 20) -> dict:
        """Returns {'items': [...], 'pagination': {'total': N, 'offset': N, 'limit': N}}"""
        resp = self._request("GET", "/users", params={"offset": offset, "limit": limit})
        if resp["status"] != 200:
            raise RuntimeError(f"list_users failed: {resp['body']}")
        return resp["body"]

    def update_user(self, user_id: int, **kwargs) -> dict:
        resp = self._request("PATCH", f"/users/{user_id}", body=kwargs)
        if resp["status"] != 200:
            raise RuntimeError(f"update_user failed: {resp['body']}")
        return resp["body"]

    def delete_user(self, user_id: int) -> bool:
        resp = self._request("DELETE", f"/users/{user_id}")
        return resp["status"] == 204

    def list_all_users(self, page_size: int = 10) -> list[dict]:
        """Fetch all users using pagination. Relies on 'offset'/'limit' in pagination."""
        all_users = []
        offset = 0
        while True:
            result = self.list_users(offset=offset, limit=page_size)
            items = result["items"]
            all_users.extend(items)
            pagination = result["pagination"]
            # Client expects 'offset' and 'limit' keys (not 'page'/'per_page')
            fetched = pagination["offset"] + len(items)
            if fetched >= pagination["total"]:
                break
            offset = fetched
        return all_users
