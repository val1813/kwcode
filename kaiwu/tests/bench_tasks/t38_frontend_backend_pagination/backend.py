"""
Frontend-backend pagination interface mismatch task.

Backend: paginated list API
Frontend client: pagination service

Bugs:
1. backend.py: returns 'total_pages' but frontend expects 'pageCount'
2. backend.py: page numbering is 0-based but frontend sends 1-based page numbers
3. frontend.py: builds query with 'per_page' but backend expects 'page_size'
"""

from typing import Any


def paginate(items: list, page: int, page_size: int) -> dict:
    """Return a paginated slice of items.

    page: 1-based page number
    page_size: items per page
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if page < 1:
        raise ValueError("page must be >= 1")

    total = len(items)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    # Bug: treats page as 0-based (should subtract 1 for 0-based slice)
    start = page * page_size
    end = start + page_size
    slice_ = items[start:end]

    return {
        "items": slice_,
        "page": page,
        # Bug: key is 'total_pages' but frontend expects 'pageCount'
        "total_pages": total_pages,
        "page_size": page_size,
        "total": total,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def get_users(page: int = 1, page_size: int = 10) -> dict:
    """Simulated user list endpoint."""
    all_users = [{"id": i, "name": f"User {i}"} for i in range(1, 51)]
    return paginate(all_users, page, page_size)


def get_posts(page: int = 1, page_size: int = 10) -> dict:
    """Simulated post list endpoint."""
    all_posts = [{"id": i, "title": f"Post {i}"} for i in range(1, 31)]
    return paginate(all_posts, page, page_size)
