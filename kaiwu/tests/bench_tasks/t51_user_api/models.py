"""
User management data models.

Bug: User.to_dict() serializes created_at as a raw datetime object instead of
an ISO 8601 string. The API layer calls to_dict() and returns it directly, so
clients receive an unserializable object rather than a string like
"2024-01-15T10:30:00".
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class User:
    id: int
    username: str
    email: str
    role: str
    created_at: datetime
    is_active: bool = True
    display_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            # Bug: should be self.created_at.isoformat() not self.created_at
            "created_at": self.created_at,
            "is_active": self.is_active,
            "display_name": self.display_name,
        }


@dataclass
class UserCreateRequest:
    username: str
    email: str
    role: str = "viewer"
    display_name: Optional[str] = None


@dataclass
class UserUpdateRequest:
    email: Optional[str] = None
    role: Optional[str] = None
    display_name: Optional[str] = None
    is_active: Optional[bool] = None


# In-memory store for tests
_users: dict[int, User] = {}
_next_id: int = 1


def reset_store() -> None:
    global _next_id
    _users.clear()
    _next_id = 1


def create_user(req: UserCreateRequest, now: datetime = None) -> User:
    global _next_id
    if now is None:
        now = datetime.utcnow()
    user = User(
        id=_next_id,
        username=req.username,
        email=req.email,
        role=req.role,
        created_at=now,
        display_name=req.display_name,
    )
    _users[_next_id] = user
    _next_id += 1
    return user


def get_user(user_id: int) -> Optional[User]:
    return _users.get(user_id)


def list_users(offset: int = 0, limit: int = 20) -> tuple[list[User], int]:
    all_users = list(_users.values())
    total = len(all_users)
    return all_users[offset: offset + limit], total


def update_user(user_id: int, req: UserUpdateRequest) -> Optional[User]:
    user = _users.get(user_id)
    if user is None:
        return None
    if req.email is not None:
        user.email = req.email
    if req.role is not None:
        user.role = req.role
    if req.display_name is not None:
        user.display_name = req.display_name
    if req.is_active is not None:
        user.is_active = req.is_active
    return user


def delete_user(user_id: int) -> bool:
    if user_id in _users:
        del _users[user_id]
        return True
    return False
