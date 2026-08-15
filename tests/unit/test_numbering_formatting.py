"""Formatting, period ordering and fingerprint logic — the pure half.

These run on the fast lane and prove no tenancy, no locking and no isolation:
those need real PostgreSQL and live in ``tests/test_numbering_isolation.py``.
What they do prove is the arithmetic the sources got wrong, where a defect is
visible without a database at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from dotmac_numbering import NumberingError, format_number, period_for, preview
from dotmac_numbering.service import request_fingerprint


class _Series:
    """The configuration surface the formatter reads. Deliberately not the ORM
    model: the formatter must not need a session to be testable."""

    def __init__(self, **kw: object) -> None:
        self.prefix = kw.get("prefix", "INV")
        self.suffix = kw.get("suffix", "")
        self.separator = kw.get("separator", "-")
        self.min_digits = kw.get("min_digits", 6)
        self.include_year = kw.get("include_year", 0)
        self.year_digits = kw.get("year_digits", 4)
        self.include_month = kw.get("include_month", 0)
        self.reset_policy = kw.get("reset_policy", "never")
        self.next_value = kw.get("next_value", 1)
        self.current_period = kw.get("current_period")


JAN = date(2026, 1, 15)


# ── Periods are ORDERED, not merely compared ────────────────────────────────


def test_periods_are_lexically_ordered_so_later_sorts_higher():
    """The whole reset decision rests on this.

    ERP compares periods for inequality, so a backdated allocation looks like a
    new period and rewinds the counter. Ordering only works if the strings are
    zero-padded — `"2026-9" > "2026-10"` is lexically true and wrong.
    """
    assert period_for(date(2026, 2, 1), "monthly") > period_for(
        date(2026, 1, 31), "monthly"
    )
    assert period_for(date(2026, 10, 1), "monthly") > period_for(
        date(2026, 9, 30), "monthly"
    )
    assert period_for(date(2027, 1, 1), "yearly") > period_for(
        date(2026, 12, 31), "yearly"
    )


def test_a_never_reset_series_has_no_period():
    assert period_for(JAN, "never") is None


def test_an_unregistered_reset_policy_fails_closed():
    with pytest.raises(NumberingError) as exc:
        period_for(JAN, "fortnightly")
    assert exc.value.code.endswith("unknown_reset_policy")


# ── One formatter ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "value", "expected"),
    [
        ({}, 1, "INV-000001"),
        ({"min_digits": 3}, 42, "INV-042"),
        ({"prefix": ""}, 7, "000007"),
        ({"suffix": "X"}, 7, "INV-000007-X"),
        ({"separator": "/"}, 7, "INV/000007"),
        ({"include_year": 1}, 7, "INV-2026-000007"),
        ({"include_year": 1, "year_digits": 2}, 7, "INV-26-000007"),
        ({"include_year": 1, "include_month": 1}, 7, "INV-2026-01-000007"),
        ({"min_digits": 2}, 12345, "INV-12345"),
    ],
)
def test_format_number_covers_the_configuration_surface(kwargs, value, expected):
    assert format_number(_Series(**kwargs), value=value, reference_date=JAN) == expected


def test_an_empty_prefix_produces_no_leading_separator():
    """A dropped empty segment, not an empty one — `-000007` is a different
    number to a human and to a unique index."""
    assert not format_number(
        _Series(prefix=""), value=7, reference_date=JAN
    ).startswith("-")


def test_a_value_below_one_is_refused():
    with pytest.raises(NumberingError) as exc:
        format_number(_Series(), value=0, reference_date=JAN)
    assert exc.value.code.endswith("invalid_value")


def test_preview_agrees_with_what_allocation_would_format():
    """ERP's preview hardcodes a four-digit segment, so for every series whose
    width is not four it shows a number that will never be issued."""
    series = _Series(min_digits=9, next_value=77)
    assert preview(series, reference_date=JAN) == format_number(
        series, value=77, reference_date=JAN
    )
    assert preview(series, reference_date=JAN) == "INV-000000077"


# ── Fingerprints ────────────────────────────────────────────────────────────


def test_the_fingerprint_covers_the_request_and_nothing_else():
    base = {"series_code": "invoice", "reference_date": JAN, "scope_segment": "t=1"}
    assert request_fingerprint(**base) == request_fingerprint(**base)
    assert request_fingerprint(**{**base, "reference_date": date(2026, 2, 1)}) != (
        request_fingerprint(**base)
    )
    assert request_fingerprint(**{**base, "series_code": "credit"}) != (
        request_fingerprint(**base)
    )
    # Scope is part of the identity: the same key in two tenants is two
    # different requests, not a replay.
    assert request_fingerprint(**{**base, "scope_segment": "t=2"}) != (
        request_fingerprint(**base)
    )
