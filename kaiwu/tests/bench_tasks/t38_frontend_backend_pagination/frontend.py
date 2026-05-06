"""Frontend pagination client."""

from typing import Any, Optional
from backend import get_users, get_posts


class PaginationClient:
    """Client that fetches paginated data from the backend."""

    def __init__(self, page_size: int = 10):
        self.page_size = page_size
        self._current_page = 1
        self._total_pages: Optional[int] = None
        self._items: list = []

    def _build_params(self, page: int) -> dict:
        # Bug: uses 'per_page' but backend expects 'page_size'
        return {"page": page, "per_page": self.page_size}

    def fetch_users(self, page: int = 1) -> dict:
        """Fetch a page of users."""
        params = self._build_params(page)
        # Simulate calling backend with params
        response = get_users(page=params["page"],
                             page_size=params.get("page_size", params.get("per_page", 10)))
        self._current_page = page
        # Bug: reads 'pageCount' but backend returns 'total_pages'
        self._total_pages = response.get("pageCount")
        self._items = response.get("items", [])
        return response

    def fetch_posts(self, page: int = 1) -> dict:
        """Fetch a page of posts."""
        params = self._build_params(page)
        response = get_posts(page=params["page"],
                             page_size=params.get("page_size", params.get("per_page", 10)))
        self._current_page = page
        self._total_pages = response.get("pageCount")
        self._items = response.get("items", [])
        return response

    def has_next(self) -> bool:
        if self._total_pages is None:
            return False
        return self._current_page < self._total_pages

    def has_prev(self) -> bool:
        return self._current_page > 1

    def next_page(self) -> Optional[dict]:
        if not self.has_next():
            return None
        return self.fetch_users(self._current_page + 1)

    def prev_page(self) -> Optional[dict]:
        if not self.has_prev():
            return None
        return self.fetch_users(self._current_page - 1)

    def all_pages(self, fetcher) -> list:
        """Fetch all pages and return combined items."""
        all_items = []
        page = 1
        while True:
            resp = fetcher(page=page)
            all_items.extend(resp.get("items", []))
            total_pages = resp.get("pageCount") or resp.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return all_items
