"""Row-count verification: a stateless external-effects contract.

`spec.py` has refused every dataset declaring `verify = [..., "row_counts"]`
with no `[backup.datasets.external_executor]` since the vocabulary was widened,
because `CatalogEvidence` (`recovery.py`) carries no row counts and nothing in
this package could observe one. That refusal was correct for what existed: a
name a descriptor could declare and nothing could satisfy is worse than a
missing name, because it reports as configured.

This module is the other half — the part that can be satisfied — built to the
same shape `exposure.py` already established for a fact this package cannot
observe by itself: a `Protocol` boundary (there, `ExposureEffects.observe()`;
here, `RowCountObserver.observe_row_counts()`) that a caller supplies, and a
set of pure functions that take an observation and a declaration and decide.
Nothing here opens a socket, restores a snapshot, or connects to a database.
The actual restore-and-count wiring is execution machinery for a different
lane; this module verifies what an observer reports and refuses when none is
given. Deliberately stateless: like `ExposureTransaction` before it was
deleted, a durable "row count verification" record owned by *this* package
would be a second copy of a fact the executor's own evidence already carries,
and the module that keeps two copies is the one where they drift.

## The six declared elements, and where each one lives

1. **Disposable restore target** — `verify_row_counts` requires a non-blank
   `restore_target` naming the target the counts were taken against, and
   refuses a blank one. A count taken against "the database" is a count taken
   against whichever database the caller happened to have open, which might be
   live; naming the disposable target is what makes that distinction
   auditable rather than assumed.

2. **Named tables** — `RowCountVerificationSpec.tables`. Declared in the
   descriptor, never discovered: this module never lists a schema's tables and
   never accepts a wildcard. A verification that counts "whatever it finds"
   cannot refuse, because there is nothing declared for the count to disagree
   with.

3. **Declared expectation source** — `RowCountExpectationSource`, a closed
   vocabulary exactly like `BackupDataset.KINDS` and `ExternalExecutorV1.kind`
   elsewhere in this package, and exactly the shape `external_recovery.py`
   already uses: `VERIFICATION_EVIDENCE` names WHAT a claim answers rather than
   embedding a literal expected value in the descriptor. A literal count in
   `deploy/product.toml` would drift the moment a migration seeds a row, and
   nobody edits a deployment descriptor on every data change; naming the
   SOURCE the expectation comes from survives that.

4. **Tolerance** — `RowCountToleranceV1`, declared and typed, BOTH absolute
   and proportional (percent), taken as the more lenient of the two rather
   than requiring both or forcing a choice. Justification: an absolute-only
   tolerance is wrong at both ends of the table-size range in this fleet's own
   descriptors — five rows of slack on a three-row lookup table demands exact
   equality in practice, while five rows of slack on a ten-million-row ledger
   table is noise pretending to be a bound. A percent-only tolerance has the
   mirror failure: 1% of three rows floors to zero, which is "exact" wearing a
   percentage sign. Declaring both and taking the looser lets a small table's
   absolute floor and a large table's proportional ceiling each do the job the
   other cannot. At least one of the two must be declared; an undeclared
   tolerance is refused at construction rather than defaulting to "exact" or
   "anything" without saying which.

5. **Observed-count evidence** — `RowCountEvidence`, returned by
   `verify_row_counts` regardless of outcome. It carries every table's
   expected count, observed count, and the tolerance that was applied, so a
   run that passed and a run that never compared read differently to whatever
   reads the evidence afterward — the property `require_row_counts_within_
   tolerance` depends on and the property the non-vacuity test below proves.

6. **Explicit refusal when executor support is absent** — the load-bearing
   arm. `verify_row_counts` raises :class:`PreconditionFailed` when `observer`
   is `None`, and again when the observer cannot produce a count for every
   declared table. Neither path returns evidence and neither path returns
   normally: this function has exactly one way to report "no observation
   occurred", and it is an exception, never an empty-but-successful result and
   never a silent return. `require_row_counts_within_tolerance` raises the
   same class again, under a different code, when a real observation
   disagrees with what was expected — so "could not observe" and "observed and
   disagreed" are distinguishable refusals, not one refusal wearing two
   meanings.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Final, Protocol

from .errors import PreconditionFailed, SpecError

__all__ = [
    "ROW_COUNT_EXPECTATION_INCOMPLETE",
    "ROW_COUNT_OBSERVATION_INCOMPLETE",
    "ROW_COUNT_OBSERVER_ABSENT",
    "ROW_COUNT_OUT_OF_TOLERANCE",
    "ROW_COUNT_TARGET_UNDECLARED",
    "RowCountEvidence",
    "RowCountExpectationSource",
    "RowCountFinding",
    "RowCountObserver",
    "RowCountToleranceV1",
    "RowCountVerificationSpec",
    "require_row_counts_within_tolerance",
    "verify_row_counts",
]

#: Stable refusal identifiers. Assert these in tests; read the prose. Same
#: convention as `spec.UNKNOWN_VERIFICATION` / `spec.UNPERFORMABLE_VERIFICATION`
#: and `external_recovery`'s `PreconditionFailed(code=...)` sites.
ROW_COUNT_TARGET_UNDECLARED: Final = "row_counts.target_undeclared"
ROW_COUNT_OBSERVER_ABSENT: Final = "row_counts.observer_absent"
ROW_COUNT_OBSERVATION_INCOMPLETE: Final = "row_counts.observation_incomplete"
ROW_COUNT_EXPECTATION_INCOMPLETE: Final = "row_counts.expectation_incomplete"
ROW_COUNT_OUT_OF_TOLERANCE: Final = "row_counts.out_of_tolerance"


class RowCountExpectationSource(str, Enum):
    """Where the expected count comes from — declared, never inferred.

    One member today, deliberately closed rather than left open: a descriptor
    naming a source this module does not recognise is refused at parse
    (`spec.py`), not silently treated as "trust the caller's number".

    ``SOURCE_SNAPSHOT``: the expected count is the count this dataset's own
    backup snapshot recorded for the table at capture time — the same
    "captured, then compared against a restore" shape `recovery.py` already
    uses for schemas and migration heads. It is supplied by the caller as
    ``expected`` in :func:`verify_row_counts`; this module does not read a
    snapshot itself, because that would make it stateful.
    """

    SOURCE_SNAPSHOT = "source_snapshot"


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountToleranceV1:
    """A declared, typed slack band. See module docstring element 4.

    ``absolute``: a row-count difference of at most this many rows.
    ``percent``: a row-count difference of at most this percentage of the
    expected count (0-100; whole numbers only — this package's TOML parser has
    no float type, and a percent that must be a whole number is not a
    limitation this contract needs to lift).

    At least one must be declared. Both may be, and the wider of the two
    computed bounds applies (see module docstring element 4 for why neither
    alone is enough across this fleet's own table sizes).
    """

    absolute: int | None = None
    percent: int | None = None

    def __post_init__(self) -> None:
        if self.absolute is None and self.percent is None:
            raise SpecError(
                "a row_count_verification tolerance must declare `absolute`, "
                "`percent`, or both. An undeclared tolerance is either 'exact' "
                "or 'anything', and leaving that unstated is exactly the "
                "ambiguity this contract exists to remove"
            )
        if self.absolute is not None and self.absolute < 0:
            raise SpecError("tolerance `absolute` must be >= 0")
        if self.percent is not None and not (0 <= self.percent <= 100):
            raise SpecError("tolerance `percent` must be between 0 and 100")

    def allowed_delta(self, expected: int) -> int:
        """The widest slack this tolerance permits for a table expected to hold
        ``expected`` rows — the greater of the absolute and proportional bounds,
        never their sum and never a default when only one was declared."""
        candidates = [0]
        if self.absolute is not None:
            candidates.append(self.absolute)
        if self.percent is not None:
            candidates.append((expected * self.percent) // 100)
        return max(candidates)


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountVerificationSpec:
    """The declared internal contract for a dataset's `row_counts` check.

    An alternative to naming an `[backup.datasets.external_executor]`: a
    dataset that declares this block instead is asking THIS package to observe
    the counts itself (via a caller-supplied :class:`RowCountObserver`) rather
    than trusting a signed receipt from elsewhere. `spec.py` accepts either —
    never neither, since `row_counts` with nobody declared to perform it is
    still refused at parse.
    """

    tables: tuple[str, ...]
    expectation_source: RowCountExpectationSource
    tolerance: RowCountToleranceV1


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountFinding:
    """One named table's comparison — expected, observed, and the bound applied."""

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
class RowCountEvidence:
    """The observed-count record this verification produces. See element 5.

    Produced whether or not every table was within tolerance — the record IS
    the audit trail, not just the verdict. `require_row_counts_within_
    tolerance` is what turns it into a refusal; nothing about producing the
    evidence is itself a pass.
    """

    dataset: str
    restore_target: str
    expectation_source: RowCountExpectationSource
    findings: tuple[RowCountFinding, ...]

    @property
    def breaches(self) -> tuple[RowCountFinding, ...]:
        return tuple(
            finding for finding in self.findings if not finding.within_tolerance
        )

    @property
    def ok(self) -> bool:
        return not self.breaches


class RowCountObserver(Protocol):
    """The effects boundary this module verifies against and never becomes.

    Exactly `exposure.ExposureEffects`'s shape: one method, implemented by
    whatever restored `tables` into a disposable target and can count rows
    there. This package ships no implementation — connecting to a real
    database is execution machinery, not a stateless contract.
    """

    def observe_row_counts(self, tables: Sequence[str]) -> Mapping[str, int]: ...


def verify_row_counts(
    *,
    dataset_code: str,
    verification: RowCountVerificationSpec,
    expected: Mapping[str, int],
    restore_target: str,
    observer: RowCountObserver | None,
) -> RowCountEvidence:
    """Observe ``verification.tables`` on ``restore_target`` and compare.

    Refuses — raises, never returns a false pass and never returns silently —
    when the target is undeclared, when no observer is installed, or when the
    observer cannot produce a count for every declared table. This is element
    6: an unobservable verification says so, in a form CI can assert both the
    exception class and the refusal identity of.
    """
    if not restore_target.strip():
        raise SpecError(
            f"dataset {dataset_code!r} row_counts verification was called with "
            "no disposable restore target named. A count taken against 'the "
            "database' is a count taken against whichever database happened "
            "to be open, which might be the live one",
            code=ROW_COUNT_TARGET_UNDECLARED,
        )
    if observer is None:
        raise PreconditionFailed(
            f"dataset {dataset_code!r} declares row_counts verification and no "
            "observer is installed to perform it against the disposable "
            f"restore target {restore_target!r}. Refusing rather than "
            "reporting success or silently skipping: an unobservable "
            "verification is not a verification that passed",
            code=ROW_COUNT_OBSERVER_ABSENT,
        )
    missing_expected = sorted(set(verification.tables) - set(expected))
    if missing_expected:
        raise SpecError(
            f"dataset {dataset_code!r} declares row_count_verification tables "
            f"{missing_expected} for which no expected count was supplied "
            f"({verification.expectation_source.value}). A table with no "
            "declared expectation cannot be compared, only asserted",
            code=ROW_COUNT_EXPECTATION_INCOMPLETE,
        )
    observed = observer.observe_row_counts(verification.tables)
    missing_observed = sorted(set(verification.tables) - set(observed))
    if missing_observed:
        raise PreconditionFailed(
            f"dataset {dataset_code!r}'s observer produced no row count for "
            f"table(s) {missing_observed} on restore target {restore_target!r}, "
            f"out of the declared {list(verification.tables)}. A partial "
            "observation is not a passed verification for the tables it never "
            "counted",
            code=ROW_COUNT_OBSERVATION_INCOMPLETE,
        )
    findings = tuple(
        RowCountFinding(
            table=table,
            expected=expected[table],
            observed=observed[table],
            allowed_delta=verification.tolerance.allowed_delta(expected[table]),
        )
        for table in verification.tables
    )
    return RowCountEvidence(
        dataset=dataset_code,
        restore_target=restore_target,
        expectation_source=verification.expectation_source,
        findings=findings,
    )


def require_row_counts_within_tolerance(evidence: RowCountEvidence) -> str:
    """Refuse unless every named table's observed count is within tolerance.

    The comparing half, split from :func:`verify_row_counts` exactly as
    `external_recovery.require_restore_proof` is split from `backup.assess`:
    one function produces evidence, the other is the caller that turns a
    disagreeable finding into a refusal. Returns a one-line summary on success
    so a caller has something to log for the audit trail alongside the
    evidence itself.
    """
    breaches = evidence.breaches
    if breaches:
        detail = "; ".join(
            f"{finding.table}: expected {finding.expected}, observed "
            f"{finding.observed} (delta {finding.delta} > allowed "
            f"{finding.allowed_delta})"
            for finding in breaches
        )
        raise PreconditionFailed(
            f"dataset {evidence.dataset!r} row_counts verification on restore "
            f"target {evidence.restore_target!r} found {len(breaches)} table(s) "
            f"outside their declared tolerance: {detail}",
            code=ROW_COUNT_OUT_OF_TOLERANCE,
        )
    return (
        f"dataset {evidence.dataset!r} row_counts verification on "
        f"{evidence.restore_target!r}: {len(evidence.findings)} table(s) within "
        f"tolerance ({evidence.expectation_source.value})"
    )
