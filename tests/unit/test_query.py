"""Tests for query helpers: pagination and ordering."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.exceptions import BadRequestError
from app.core.query import apply_ordering
from app.models.person import Person


def test_apply_ordering_noop_when_order_by_none():
    """Verify apply_ordering is a no-op when order_by is None."""
    stmt = select(Person)
    result = apply_ordering(stmt, Person, None, allowed={"email", "first_name"})
    # Statement should be unchanged (no ORDER BY clause)
    assert "ORDER BY" not in str(result)
    assert result == stmt


def test_apply_ordering_noop_when_order_by_empty():
    """Verify apply_ordering is a no-op when order_by is empty string."""
    stmt = select(Person)
    result = apply_ordering(stmt, Person, "", allowed={"email", "first_name"})
    # Statement should be unchanged (no ORDER BY clause)
    assert "ORDER BY" not in str(result)
    assert result == stmt


def test_apply_ordering_applies_when_allowed():
    """Verify apply_ordering applies ordering when column is allowed."""
    stmt = select(Person)
    result = apply_ordering(stmt, Person, "email", allowed={"email", "first_name"})
    # Statement should contain ORDER BY
    assert "ORDER BY" in str(result)
    # Verify the compiled statement is different from the original
    assert result != stmt


def test_apply_ordering_raises_when_disallowed():
    """Verify apply_ordering raises BadRequestError when column is disallowed."""
    stmt = select(Person)
    with pytest.raises(BadRequestError) as exc_info:
        apply_ordering(stmt, Person, "password", allowed={"email", "first_name"})
    assert "Cannot order by" in str(exc_info.value)
    assert "password" in str(exc_info.value)
