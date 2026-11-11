"""Tests for query helpers: pagination and ordering."""

from __future__ import annotations

import pytest
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.models import Party
from dotmac_kernel.query import apply_ordering, apply_pagination, pagination_context
from sqlalchemy import select


def test_apply_ordering_noop_when_order_by_none():
    """Verify apply_ordering is a no-op when order_by is None."""
    stmt = select(Party)
    result = apply_ordering(stmt, Party, None, allowed={"email", "display_name"})
    # Statement should be unchanged (no ORDER BY clause)
    assert "ORDER BY" not in str(result)
    assert result == stmt


def test_apply_ordering_noop_when_order_by_empty():
    """Verify apply_ordering is a no-op when order_by is empty string."""
    stmt = select(Party)
    result = apply_ordering(stmt, Party, "", allowed={"email", "display_name"})
    # Statement should be unchanged (no ORDER BY clause)
    assert "ORDER BY" not in str(result)
    assert result == stmt


def test_apply_ordering_applies_when_allowed():
    """Verify apply_ordering applies ordering when column is allowed."""
    stmt = select(Party)
    result = apply_ordering(stmt, Party, "email", allowed={"email", "display_name"})
    # Statement should contain ORDER BY
    assert "ORDER BY" in str(result)
    # Verify the compiled statement is different from the original
    assert result != stmt


def test_apply_ordering_raises_when_disallowed():
    """Verify apply_ordering raises BadRequestError when column is disallowed."""
    stmt = select(Party)
    with pytest.raises(BadRequestError) as exc_info:
        apply_ordering(stmt, Party, "password", allowed={"email", "display_name"})
    assert "Cannot order by" in str(exc_info.value)
    assert "password" in str(exc_info.value)


def test_pagination_context_middle_page():
    ctx = pagination_context(total=95, limit=10, offset=40)
    assert ctx["current_page"] == 5
    assert ctx["total_pages"] == 10
    assert ctx["page_items"] == [1, None, 4, 5, 6, None, 10]
    assert ctx["page_start"] == 41
    assert ctx["page_end"] == 50
    assert ctx["has_prev"] is True
    assert ctx["has_next"] is True


def test_pagination_context_empty_and_last_page():
    empty = pagination_context(total=0, limit=10, offset=0)
    assert empty["page_start"] == 0
    assert empty["page_end"] == 0
    assert empty["page_items"] == [1]
    assert empty["current_page"] == 1
    assert empty["has_next"] is False

    last = pagination_context(total=95, limit=10, offset=90)
    assert last["current_page"] == 10
    assert last["page_end"] == 95
    assert last["has_next"] is False


def test_pagination_context_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="limit"):
        pagination_context(total=5, limit=0, offset=0)


@pytest.mark.parametrize("offset", [95, 100, 1000])
def test_pagination_context_stale_offset_preserves_query_and_recovers(offset):
    ctx = pagination_context(total=95, limit=10, offset=offset)
    assert ctx["offset"] == offset
    stmt = apply_pagination(select(Party), limit=ctx["limit"], offset=ctx["offset"])
    assert f"OFFSET {offset}" in str(
        stmt.compile(compile_kwargs={"literal_binds": True})
    )
    assert ctx["current_page"] is None
    assert ctx["page_start"] == ctx["page_end"] == 0
    assert ctx["page_items"] == [1, None, 9, 10]
    assert ctx["prev_offset"] == 90
    assert ctx["has_prev"] is True
    assert ctx["has_next"] is False


@pytest.mark.parametrize("offset, previous", [(10, 0), (1000, 0)])
def test_pagination_context_empty_with_stale_offset(offset, previous):
    ctx = pagination_context(total=0, limit=10, offset=offset)
    assert ctx["offset"] == offset
    assert ctx["current_page"] is None
    assert ctx["page_start"] == ctx["page_end"] == 0
    assert ctx["page_items"] == [1]
    assert ctx["prev_offset"] == previous
    assert ctx["has_prev"] is True
    assert ctx["has_next"] is False


@pytest.mark.parametrize("total, offset, field", [(-1, 0, "total"), (5, -1, "offset")])
def test_pagination_context_rejects_negative_inputs(total, offset, field):
    with pytest.raises(ValueError, match=field):
        pagination_context(total=total, limit=10, offset=offset)
