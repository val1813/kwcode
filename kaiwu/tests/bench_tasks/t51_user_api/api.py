"""
User management API endpoints.

Bug: The list endpoint uses offset/limit query parameters internally, but the
pagination metadata returned to the client uses 'page' and 'per_page' keys
instead of 'offset' and 'limit'. The client SDK expects 'offset'/'limit' in
the pagination object to know how to fetch the next page.
"""

from datetime import datetime
from typing import Any
from models import (
    UserCreateRequest,
    UserUpdateRequest,
    create_user,
    get_user,
    list_users,
    update_user,
    delete_user,
)


def handle_request(method: str, path: str, body: dict = None,
                   params: dict = None) -> dict:
    """Dispatch a simulated HTTP request. Returns a response dict with
    'status' (int) and 'body' (dict)."""
    body = body or {}
    params = params or {}

    if method == "POST" and path == "/users":
        return _create_user(body)
    if method == "GET" and path == "/users":
        return _list_users(params)
    if method == "GET" and path.startswith("/users/"):
        user_id = int(path.split("/")[2])
        return _get_user(user_id)
    if method == "PATCH" and path.startswith("/users/"):
        user_id = int(path.split("/")[2])
        return _update_user(user_id, body)
    if method == "DELETE" and path.startswith("/users/"):
        user_id = int(path.split("/")[2])
        return _delete_user(user_id)
    return {"status": 404, "body": {"error": "not found"}}


def _create_user(body: dict) -> dict:
    if not body.get("username") or not body.get("email"):
        return {"status": 400, "body": {"error": "username and email required"}}
    req = UserCreateRequest(
        username=body["username"],
        email=body["email"],
        role=body.get("role", "viewer"),
        display_name=body.get("display_name"),
    )
    now_str = body.get("_now")
    now = datetime.fromisoformat(now_str) if now_str else None
    user = create_user(req, now=now)
    return {"status": 201, "body": user.to_dict()}


def _list_users(params: dict) -> dict:
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 20))
    users, total = list_users(offset=offset, limit=limit)

    # Bug: pagination keys should be 'offset' and 'limit', not 'page'/'per_page'
    return {
        "status": 200,
        "body": {
            "items": [u.to_dict() for u in users],
            "pagination": {
                "total": total,
                "page": offset,        # Bug: should be "offset": offset
                "per_page": limit,     # Bug: should be "limit": limit
            },
        },
    }


def _get_user(user_id: int) -> dict:
    user = get_user(user_id)
    if user is None:
        return {"status": 404, "body": {"error": "user not found"}}
    return {"status": 200, "body": user.to_dict()}


def _update_user(user_id: int, body: dict) -> dict:
    req = UserUpdateRequest(
        email=body.get("email"),
        role=body.get("role"),
        display_name=body.get("display_name"),
        is_active=body.get("is_active"),
    )
    user = update_user(user_id, req)
    if user is None:
        return {"status": 404, "body": {"error": "user not found"}}
    return {"status": 200, "body": user.to_dict()}


def _delete_user(user_id: int) -> dict:
    ok = delete_user(user_id)
    if not ok:
        return {"status": 404, "body": {"error": "user not found"}}
    return {"status": 204, "body": {}}
