"""Formatting, period identity and configuration validation — the pure half.

These prove no tenancy, no locking and no isolation: those need real PostgreSQL
and live in ``tests/test_numbering_isolation.py``. What they do prove is the
arithmetic and the validation, where a defect is visible without a database.
"""

from __future__ import annotations

from datetime import date

import pytest
from dotmac_numbering import (
    NO_PERIOD,
    NumberingError,
    SeriesConfiguration,
    format_number,
    period_for,
)


class _Series:
    """The configuration surface the formatter reads.

    Deliberately not the ORM model: the formatter must be testable without a
    session, or it will grow a dependency on one.
    """

    def __init__(self, **kw: object) -> None:
        self.prefix = kw.get("prefix", "INV")
        self.suffix = kw.get("suffix", "")
        self.separator = kw.get("separator", "-")
        self.min_digits = kw.get("min_digits", 6)
        self.include_year = kw.get("include_year", 0)
        self.year_digits = kw.get("year_digits", 4)
        self.include_month = kw.get("include_month", 0)


JAN = date(2026, 1, 15)


# ── Period identity ─────────────────────────────────────────────────────────


def test_a_non_resetting_series_has_a_total_period_key():
    """`*`, not null. Every unique key including the period stays total."""
    assert period_for(JAN, "never") == NO_PERIOD


def test_periods_are_zero_padded_so_ordering_is_lexical():
    """Ordering is what stops a backdated allocation restarting a sequence, and
    it only holds if the strings are padded — `"2026-9" > "2026-10"` is
    lexically true and wrong."""
    assert period_for(date(2026, 2, 1), "monthly") > period_for(
        date(2026, 1, 31), "monthly"
    )
    assert period_for(date(2026, 10, 1), "monthly") > period_for(
        date(2026, 9, 30), "monthly"
    )
    assert period_for(date(2027, 1, 1), "yearly") > period_for(
        date(2026, 12, 31), "yearly"
    )


def test_the_period_identifies_the_number_not_the_clock():
    """A 2026 date yields the 2026 period whenever it is presented. This is the
    property that lets a backdated allocation read its own counter."""
    assert period_for(date(2026, 3, 4), "yearly") == "2026"
    assert period_for(date(2026, 3, 4), "monthly") == "2026-03"


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
    assert not format_number(
        _Series(prefix=""), value=7, reference_date=JAN
    ).startswith("-")


def test_a_value_below_one_is_refused():
    with pytest.raises(NumberingError) as exc:
        format_number(_Series(), value=0, reference_date=JAN)
    assert exc.value.code.endswith("invalid_value")


# ── Configuration validation ────────────────────────────────────────────────


def _config(**kw: object) -> SeriesConfiguration:
    base: dict[str, object] = {"series_code": "invoice"}
    base.update(kw)
    return SeriesConfiguration(**base)  # type: ignore[arg-type]


def test_a_valid_configuration_passes():
    _config().validate()
    _config(reset_policy="yearly", include_year=True).validate()
    _config(reset_policy="monthly", include_year=True, include_month=True).validate()


def test_a_yearly_reset_must_print_the_year():
    """Otherwise every January reissues the strings of the year before, and the
    number stops identifying the document."""
    with pytest.raises(NumberingError) as exc:
        _config(reset_policy="yearly").validate()
    assert exc.value.code.endswith("incoherent_reset")


def test_a_monthly_reset_must_print_year_and_month():
    with pytest.raises(NumberingError) as exc:
        _config(reset_policy="monthly", include_year=True).validate()
    assert exc.value.code.endswith("incoherent_reset")


def test_a_month_without_a_year_is_refused():
    with pytest.raises(NumberingError) as exc:
        _config(include_month=True).validate()
    assert exc.value.code.endswith("incoherent_format")


@pytest.mark.parametrize("width", [0, -1, 19, 100])
def test_an_impossible_digit_width_is_refused(width):
    with pytest.raises(NumberingError) as exc:
        _config(min_digits=width).validate()
    assert exc.value.code.endswith("invalid_min_digits")


@pytest.mark.parametrize("digits", [1, 3, 5])
def test_an_unsupported_year_width_is_refused(digits):
    with pytest.raises(NumberingError) as exc:
        _config(year_digits=digits).validate()
    assert exc.value.code.endswith("invalid_year_digits")


@pytest.mark.parametrize("start", [0, -5])
def test_a_non_positive_start_value_is_refused(start):
    with pytest.raises(NumberingError) as exc:
        _config(start_value=start).validate()
    assert exc.value.code.endswith("invalid_start_value")


def test_an_empty_or_oversized_series_code_is_refused():
    with pytest.raises(NumberingError):
        _config(series_code="").validate()
    with pytest.raises(NumberingError):
        _config(series_code="x" * 81).validate()
