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
