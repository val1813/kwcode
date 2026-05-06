"""Tests for user management API interface consistency (t57)."""

import pytest
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import models
from client import UserClient


@pytest.fixture(autouse=True)
def reset():
    models.reset_store()
    yield
    models.reset_store()


FIXED_NOW = "2024-01-15T10:30:00"


class TestUserModelSerialization:
    def test_created_at_is_string(self):
        """created_at must serialize to an ISO string, not a datetime object."""
        from models import UserCreateRequest, create_user
        req = UserCreateRequest(username="alice", email="alice@example.com")
        user = create_user(req, now=datetime.fromisoformat(FIXED_NOW))
        d = user.to_dict()
        assert isinstance(d["created_at"], str), (
            f"created_at should be a string, got {type(d['created_at'])}"
        )

    def test_created_at_iso_format(self):
        """created_at string must be parseable as ISO 8601."""
        from models import UserCreateRequest, create_user
        req = UserCreateRequest(username="bob", email="bob@example.com")
        user = create_user(req, now=datetime.fromisoformat(FIXED_NOW))
        d = user.to_dict()
        parsed = datetime.fromisoformat(d["created_at"])
        assert parsed == datetime.fromisoformat(FIXED_NOW)

    def test_to_dict_contains_expected_keys(self):
        from models import UserCreateRequest, create_user
        req = UserCreateRequest(username="carol", email="carol@example.com", role="admin")
        user = create_user(req, now=datetime.fromisoformat(FIXED_NOW))
        d = user.to_dict()
        for key in ("id", "username", "email", "role", "created_at", "is_active"):
            assert key in d


class TestListPagination:
    def test_pagination_has_offset_key(self):
        """List response pagination must use 'offset', not 'page'."""
        client = UserClient()
        client.create_user("u1", "u1@x.com", _now=FIXED_NOW)
        result = client.list_users(offset=0, limit=10)
        pagination = result["pagination"]
        assert "offset" in pagination, (
            f"pagination must have 'offset' key, got keys: {list(pagination.keys())}"
        )

    def test_pagination_has_limit_key(self):
        """List response pagination must use 'limit', not 'per_page'."""
        client = UserClient()
        client.create_user("u1", "u1@x.com", _now=FIXED_NOW)
        result = client.list_users(offset=0, limit=10)
        pagination = result["pagination"]
        assert "limit" in pagination, (
            f"pagination must have 'limit' key, got keys: {list(pagination.keys())}"
        )

    def test_pagination_offset_value(self):
        client = UserClient()
        for i in range(5):
            client.create_user(f"u{i}", f"u{i}@x.com", _now=FIXED_NOW)
        result = client.list_users(offset=2, limit=2)
        assert result["pagination"]["offset"] == 2
        assert result["pagination"]["limit"] == 2
        assert result["pagination"]["total"] == 5

    def test_list_all_users_pagination(self):
        """list_all_users() must work end-to-end using offset/limit pagination."""
        client = UserClient()
        for i in range(7):
            client.create_user(f"user{i}", f"user{i}@x.com", _now=FIXED_NOW)
        all_users = client.list_all_users(page_size=3)
        assert len(all_users) == 7


class TestCreateAndGet:
    def test_create_returns_string_created_at(self):
        client = UserClient()
        user = client.create_user("alice", "alice@example.com", _now=FIXED_NOW)
        assert isinstance(user["created_at"], str)

    def test_get_user_returns_string_created_at(self):
        client = UserClient()
        created = client.create_user("alice", "alice@example.com", _now=FIXED_NOW)
        fetched = client.get_user(created["id"])
        assert isinstance(fetched["created_at"], str)

    def test_create_and_get_roundtrip(self):
        client = UserClient()
        created = client.create_user("dave", "dave@example.com", role="editor",
                                     _now=FIXED_NOW)
        fetched = client.get_user(created["id"])
        assert fetched["username"] == "dave"
        assert fetched["role"] == "editor"
        assert fetched["created_at"] == FIXED_NOW

    def test_update_user(self):
        client = UserClient()
        created = client.create_user("eve", "eve@example.com", _now=FIXED_NOW)
        updated = client.update_user(created["id"], email="eve2@example.com")
        assert updated["email"] == "eve2@example.com"

    def test_delete_user(self):
        client = UserClient()
        created = client.create_user("frank", "frank@example.com", _now=FIXED_NOW)
        assert client.delete_user(created["id"]) is True
        assert client.get_user(created["id"]) is None
