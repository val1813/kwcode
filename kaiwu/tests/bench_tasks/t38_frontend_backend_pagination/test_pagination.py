"""Tests for frontend-backend pagination interface consistency."""

import pytest
from backend import paginate, get_users, get_posts
from frontend import PaginationClient


class TestBackendPaginate:
    def test_first_page_returns_correct_items(self):
        items = list(range(1, 26))  # 25 items
        result = paginate(items, page=1, page_size=10)
        assert result["items"] == list(range(1, 11))

    def test_second_page_returns_correct_items(self):
        items = list(range(1, 26))
        result = paginate(items, page=2, page_size=10)
        assert result["items"] == list(range(11, 21))

    def test_last_page_partial(self):
        items = list(range(1, 26))
        result = paginate(items, page=3, page_size=10)
        assert result["items"] == list(range(21, 26))

    def test_response_has_page_count_key(self):
        """Backend must return 'pageCount' for frontend compatibility."""
        result = paginate(list(range(25)), page=1, page_size=10)
        assert "pageCount" in result, "Backend must return 'pageCount' not 'total_pages'"

    def test_total_pages_calculation(self):
        items = list(range(25))
        result = paginate(items, page=1, page_size=10)
        assert result["pageCount"] == 3

    def test_has_next_and_prev(self):
        items = list(range(25))
        p1 = paginate(items, page=1, page_size=10)
        assert p1["has_next"] is True
        assert p1["has_prev"] is False

        p3 = paginate(items, page=3, page_size=10)
        assert p3["has_next"] is False
        assert p3["has_prev"] is True

    def test_page_numbering_is_1_based(self):
        """Page 1 should return the first 10 items, not skip them."""
        items = list(range(1, 51))
        result = paginate(items, page=1, page_size=10)
        assert result["items"][0] == 1

    def test_invalid_page_raises(self):
        with pytest.raises(ValueError):
            paginate([], page=0, page_size=10)

    def test_invalid_page_size_raises(self):
        with pytest.raises(ValueError):
            paginate([], page=1, page_size=0)


class TestGetUsers:
    def test_returns_50_total(self):
        result = get_users(page=1, page_size=10)
        assert result["total"] == 50

    def test_first_page_first_user(self):
        result = get_users(page=1, page_size=10)
        assert result["items"][0]["id"] == 1

    def test_page_5_last_page(self):
        result = get_users(page=5, page_size=10)
        assert len(result["items"]) == 10
        assert result["has_next"] is False


class TestFrontendPaginationClient:
    def test_fetch_users_first_page(self):
        client = PaginationClient(page_size=10)
        resp = client.fetch_users(page=1)
        assert len(resp["items"]) == 10
        assert resp["items"][0]["id"] == 1

    def test_client_knows_total_pages(self):
        client = PaginationClient(page_size=10)
        client.fetch_users(page=1)
        assert client._total_pages == 5

    def test_has_next_after_first_page(self):
        client = PaginationClient(page_size=10)
        client.fetch_users(page=1)
        assert client.has_next() is True

    def test_no_next_on_last_page(self):
        client = PaginationClient(page_size=10)
        client.fetch_users(page=5)
        assert client.has_next() is False

    def test_all_pages_returns_all_users(self):
        client = PaginationClient(page_size=10)
        all_users = client.all_pages(get_users)
        assert len(all_users) == 50

    def test_all_pages_returns_all_posts(self):
        client = PaginationClient(page_size=10)
        all_posts = client.all_pages(get_posts)
        assert len(all_posts) == 30
