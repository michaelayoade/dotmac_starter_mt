"""Query helpers: pagination and ordering.

Ported from dotmac_sub:app/services/common.py (org infrastructure source of
truth) — only `apply_pagination` and `apply_ordering` are carried over;
`validate_enum` and the other helpers in that module are skipped (YAGNI,
no Task 7 service needs them yet).

Adaptation note (see task-6-report.md PORT-DELTA for detail): SUB's
`apply_ordering(query, order_by, order_dir, allowed_columns: dict)` raises an
HTTP-layer 400 error on an invalid column, which cannot be carried over
verbatim — services must not speak HTTP (same rule applied to crud.py), and
this task's verification gate forbids that HTTP exception type appearing
anywhere in app/core/query.py. The signature here instead matches this
task's declared interface (`apply_ordering(stmt, model, order_by, allowed:
set[str])`) and silently no-ops on an unset/disallowed `order_by` rather
than raising, since a query helper has no HTTP-facing error channel.
Callers that need to reject bad `order_by` values with a 400 should
validate against `allowed` themselves before calling this helper.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select

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


def apply_ordering(
    stmt: Select, model: type[T], order_by: str | None, allowed: set[str]
) -> Select:
    """Apply ordering to a select statement, validated against an allow-list.

    Args:
        stmt: SQLAlchemy select statement.
        model: Model class to resolve the ordering column from.
        order_by: Column name to order by (None or not in `allowed` is a no-op).
        allowed: Set of column names permitted for ordering.

    Returns:
        Statement with ordering applied, or the statement unchanged if
        `order_by` is None or not in `allowed`.
    """
    if not order_by or order_by not in allowed:
        return stmt
    column = getattr(model, order_by)
    return stmt.order_by(column)
