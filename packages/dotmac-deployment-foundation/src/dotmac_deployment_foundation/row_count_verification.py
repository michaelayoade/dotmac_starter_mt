"""Pure row-count comparison, deliberately not a deployment verification.

Foundation has no trusted, reachable production executor that can restore a
backup into an isolated target, read a backup-owned source snapshot, and obtain
the observed counts. Consequently this module has no descriptor schema,
entry-point discovery, observer protocol, evidence receipt, or a function
that turns a result into a deployment verdict. Adding any of those would make
a declaration or caller-constructed value look like a performed deployment
verification.

compare_row_counts is useful only as deterministic arithmetic for the future
execution owner. Its result cannot satisfy
BackupDataset.verify. Until that owner exists, row_counts continues to be
refused by spec.UNPERFORMABLE_VERIFICATION unless a signed external executor
owns the verification.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Final

from .errors import SpecError

__all__ = [
    "ROW_COUNT_EXPECTATION_INCOMPLETE",
    "ROW_COUNT_OBSERVATION_INCOMPLETE",
    "ROW_COUNT_TABLES_EMPTY",
    "ROW_COUNT_TABLES_DUPLICATE",
    "ROW_COUNT_TOLERANCE_INVALID",
    "ROW_COUNT_VALUE_INVALID",
    "RowCountComparison",
    "RowCountDifference",
    "RowCountToleranceV1",
    "compare_row_counts",
]

ROW_COUNT_TABLES_EMPTY: Final = "row_counts.tables_empty"
ROW_COUNT_TABLES_DUPLICATE: Final = "row_counts.tables_duplicate"
ROW_COUNT_TOLERANCE_INVALID: Final = "row_counts.tolerance_invalid"
ROW_COUNT_EXPECTATION_INCOMPLETE: Final = "row_counts.expectation_incomplete"
ROW_COUNT_OBSERVATION_INCOMPLETE: Final = "row_counts.observation_incomplete"
ROW_COUNT_VALUE_INVALID: Final = "row_counts.value_invalid"


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountToleranceV1:
    """A declared arithmetic tolerance; it neither performs nor proves a run."""

    absolute: int | None = None
    percent: int | None = None

    def __post_init__(self) -> None:
        if self.absolute is None and self.percent is None:
            raise SpecError(
                "a row-count tolerance needs absolute, percent, or both",
                code=ROW_COUNT_TOLERANCE_INVALID,
            )
        if self.absolute is not None and (
            isinstance(self.absolute, bool)
            or not isinstance(self.absolute, int)
            or self.absolute < 0
        ):
            raise SpecError(
                "row-count tolerance absolute must be a non-negative integer",
                code=ROW_COUNT_TOLERANCE_INVALID,
            )
        if self.percent is not None and (
            isinstance(self.percent, bool)
            or not isinstance(self.percent, int)
            or not 0 <= self.percent <= 100
        ):
            raise SpecError(
                "row-count tolerance percent must be an integer from 0 to 100",
                code=ROW_COUNT_TOLERANCE_INVALID,
            )

    def allowed_delta(self, expected: int) -> int:
        return max(
            self.absolute or 0,
            (expected * self.percent) // 100 if self.percent is not None else 0,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountDifference:
    """One arithmetic result, not an observation receipt."""

    table: str
    expected: int
    observed: int
    allowed_delta: int

    @property
    def delta(self) -> int:
        return abs(self.observed - self.expected)

    @property
    def within_tolerance(self) -> bool:
        return self.delta <= self.allowed_delta


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountComparison:
    """Deterministic output for a future, trusted execution owner.

    This value deliberately carries no target identity, timestamp, snapshot
    identity, observer identity, signature, or success assertion. A caller can
    construct it, so it must never be accepted as deployment evidence.
    """

    differences: tuple[RowCountDifference, ...]

    @property
    def breaches(self) -> tuple[RowCountDifference, ...]:
        return tuple(item for item in self.differences if not item.within_tolerance)

    @property
    def within_tolerance(self) -> bool:
        return not self.breaches


def _require_count(value: object, *, table: str, side: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpecError(
            f"row-count {side} value for {table!r} must be a non-negative integer",
            code=ROW_COUNT_VALUE_INVALID,
        )
    return value


def _require_mapping(value: object, *, side: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise SpecError(
            f"row-count {side} values must be a mapping keyed by declared table",
            code=ROW_COUNT_VALUE_INVALID,
        )
    return value


def compare_row_counts(
    *,
    tables: Sequence[str],
    expected: Mapping[str, int],
    observed: Mapping[str, int],
    tolerance: RowCountToleranceV1,
) -> RowCountComparison:
    """Compare supplied counts without claiming where either side came from."""

    if isinstance(tables, str | bytes) or not isinstance(tables, Sequence):
        raise SpecError(
            "row-count comparison tables must be a sequence of table names, "
            "not a scalar or arbitrary iterable",
            code=ROW_COUNT_VALUE_INVALID,
        )
    if not isinstance(tolerance, RowCountToleranceV1):
        raise SpecError(
            "row-count comparison tolerance must be RowCountToleranceV1",
            code=ROW_COUNT_TOLERANCE_INVALID,
        )
    named_tables = tuple(tables)
    if not named_tables:
        raise SpecError(
            "a row-count comparison needs at least one named table",
            code=ROW_COUNT_TABLES_EMPTY,
        )
    if any(not isinstance(table, str) or not table.strip() for table in named_tables):
        raise SpecError(
            "row-count comparison table names must be non-blank strings",
            code=ROW_COUNT_VALUE_INVALID,
        )
    duplicates = sorted(
        {table for table in named_tables if named_tables.count(table) > 1}
    )
    if duplicates:
        raise SpecError(
            f"row-count comparison names tables more than once: {duplicates}",
            code=ROW_COUNT_TABLES_DUPLICATE,
        )

    expected_values = _require_mapping(expected, side="expected")
    observed_values = _require_mapping(observed, side="observed")
    missing_expected = sorted(set(named_tables) - set(expected_values))
    if missing_expected:
        raise SpecError(
            f"row-count comparison has no expected count for {missing_expected}",
            code=ROW_COUNT_EXPECTATION_INCOMPLETE,
        )
    missing_observed = sorted(set(named_tables) - set(observed_values))
    if missing_observed:
        raise SpecError(
            f"row-count comparison has no observed count for {missing_observed}",
            code=ROW_COUNT_OBSERVATION_INCOMPLETE,
        )

    differences = tuple(
        RowCountDifference(
            table=table,
            expected=_require_count(
                expected_values[table], table=table, side="expected"
            ),
            observed=_require_count(
                observed_values[table], table=table, side="observed"
            ),
            allowed_delta=tolerance.allowed_delta(
                _require_count(expected_values[table], table=table, side="expected")
            ),
        )
        for table in named_tables
    )
    return RowCountComparison(differences=differences)
