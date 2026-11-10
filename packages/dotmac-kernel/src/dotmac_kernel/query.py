"""Query helpers: pagination and ordering.

Ported from dotmac_sub:app/services/common.py (org infrastructure source of
truth) — only `apply_pagination` and `apply_ordering` are carried over;
`validate_enum` and the other helpers in that module are skipped (YAGNI,
no Task 7 service needs them yet).

Adaptation note (see task-6-report.md PORT-DELTA for detail): SUB's
`apply_ordering(query, order_by, order_dir, allowed_columns: dict)` raises an
HTTP-layer 400 error on an invalid column. Task 6 now applies the same
requirement here: raise BadRequestError (service-layer exception) when
`order_by` is non-empty but not in `allowed`, following the decision to
reject invalid ordering early. Falsy `order_by` (None/empty string) is a
no-op, since it indicates the caller did not request ordering.
"""

from __future__ import annotations

from math import ceil
from typing import TypeVar

from sqlalchemy import Select

from dotmac_kernel.exceptions import BadRequestError

T = TypeVar("T")


def apply_pagination(stmt: Select, *, limit: int, offset: int) -> Select:
    """Apply pagination to a select statement.

    Args:
        stmt: SQLAlchemy select statement.
        limit: Maximum number of results.
        offset: Number of results to skip.

    Returns:
        Statement with pagination applied.
    """
    return stmt.limit(limit).offset(offset)


def escape_like(value: str) -> str:
    """Escape SQL LIKE/ILIKE wildcards (`%`, `_`) and the escape character
    itself (`\\`) so a caller's free-text search term matches literal
    characters, not LIKE metacharacters (e.g. a search for `"50%"` should
    match only the literal string `50%`, not any string starting with `50`).

    Single owner (SoT) for this three-`.replace()` sequence — Task 6 pulled
    it out of `app.features.parties.service._search_filter` (which used to
    inline it) into core specifically so `app.features.rbac.service.
    list_grantable_parties` can reach it too: `rbac` cannot import
    `parties.service._search_filter` (it's feature-private, and features
    never import each other), but both features may import core.

    Callers still wrap the result in `%...%` (or a prefix/suffix-only
    pattern) and pass `escape="\\\\"` to `.ilike()`/`.like()` themselves —
    this function only escapes the value.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_ordering(
    stmt: Select, model: type[T], order_by: str | None, allowed: set[str]
) -> Select:
    """Apply ordering to a select statement, validated against an allow-list.

    Args:
        stmt: SQLAlchemy select statement.
        model: Model class to resolve the ordering column from.
        order_by: Column name to order by (None or empty string is a no-op).
        allowed: Set of column names permitted for ordering.

    Returns:
        Statement with ordering applied.

    Raises:
        BadRequestError: If order_by is non-empty but not in allowed.
    """
    if not order_by:
        return stmt
    if order_by not in allowed:
        raise BadRequestError(f"Cannot order by {order_by!r}")
    column = getattr(model, order_by)
    return stmt.order_by(column)


def pagination_context(*, total: int, limit: int, offset: int) -> dict[str, object]:
    """Display and navigation values for a server-rendered list page.

    The template half of this contract is the `table_pagination` macro in
    `templates/components/table_macros.html`, which reads exactly the keys
    returned here. Promoted from Academy's product copy so every assembly
    renders list pagination the same way instead of re-deriving page math
    per route. `page_items` carries `None` where an ellipsis belongs.
    The requested query offset is preserved. An out-of-range offset shows
    0-0 with no active page; navigation stays near the last valid page and
    Previous recovers there directly for a nonempty collection.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if offset < 0:
        raise ValueError("offset must not be negative")
    if total < 0:
        raise ValueError("total must not be negative")
    total_pages = max(1, ceil(total / limit))
    out_of_range = offset >= total if total else offset > 0
    window_page = min(total_pages, (offset // limit) + 1)
    current_page = None if out_of_range else window_page
    wanted = {1, total_pages}
    wanted.update(range(max(1, window_page - 1), min(total_pages, window_page + 1) + 1))
    page_items: list[int | None] = []
    previous = 0
    for page in sorted(wanted):
        if previous and page - previous > 1:
            page_items.append(None)
        page_items.append(page)
        previous = page
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "current_page": current_page,
        "total_pages": total_pages,
        "page_items": page_items,
        "page_start": offset + 1 if total and not out_of_range else 0,
        "page_end": min(offset + limit, total) if not out_of_range else 0,
        "prev_offset": (total_pages - 1) * limit
        if out_of_range
        else max(offset - limit, 0),
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
    }


__all__ = [
    "apply_pagination",
    "escape_like",
    "pagination_context",
]
