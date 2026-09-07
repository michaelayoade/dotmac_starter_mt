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

## Revision: a declared block is not a performer

The first version of this module let `[backup.datasets.row_count_verification]`
satisfy `row_counts` at parse purely by existing — tables, an expectation
source, a tolerance. A reviewer measured what that actually proved: nothing.
`spec.py` never executes anything, so a block with the right shape and zero
installed capability behind it parsed exactly as cleanly as a real one — "a
check that answers without being able to refuse", moved to parse time. Four
findings followed, and this revision is the repair for each:

1. **Declaration substitutes for a performer.** A dataset now names an
   `[backup.datasets.row_count_verification.observer]` IDENTITY (`identifier`,
   `version` — the same idea as `ExternalExecutorV1`, minus a signing key this
   module has no receipt to bind), and `spec.py` refuses the descriptor unless
   that identifier is DECLARED by an installed distribution's entry point in
   :data:`ROW_COUNT_OBSERVER_ENTRY_POINT_GROUP` — the exact metadata-only
   discovery `execution_bindings.declared_provider_names` already uses for
   `--provider`, reused via `discovery.declared_names` rather than a second
   copy of it (see that module's docstring on why a second copy is a second
   authority). This still does not *execute* the observer — `validate` must
   not import assembly code — but it proves the environment claims to carry
   one, which a bare TOML block never did.

2. **Two performers may coexist.** Nothing previously stopped a dataset
   declaring both `external_executor` and `row_count_verification` — two
   authorities for one verification, with no rule for which one's evidence
   counts or what a disagreement between them would even mean. `spec.py` now
   refuses that combination outright (`ROW_COUNT_DUAL_PERFORMER`).

3. **Evidence remains caller-constructible.** True, and only partly
   closeable here — see "What this module does NOT prove" below.

4. **Snapshot/target provenance is absent.** `expected` used to be a bare
   `Mapping[str, int]` naming a *kind* of source (`source_snapshot`) and no
   *particular* one. It is now :class:`RowCountSnapshotV1` — a named,
   timestamped snapshot bound to the dataset it describes — and
   `verify_row_counts` takes `now_epoch` for the observation's own timestamp,
   so :class:`RowCountEvidence` records which snapshot supplied the expected
   counts and when the observation happened, alongside which restore target.
   An in-tolerance verdict is now re-derivable and disputable after the fact:
   a reader can ask "which snapshot, observed when, against what target" and
   get an answer, rather than a bare pass.

## What this module does NOT prove

Stated because a provenance record read as an independence guarantee is worse
than no record — the same discipline `host_source.py` applies to its own
types. `RowCountSnapshotV1`, `RowCountObserver`/its returned counts, and
`RowCountEvidence` are PARSING AND CARRYING types. Like
`host_source.CandidateReceipt` and `host_source.InstalledArtifact` — both
plain, freely constructible dataclasses that "how a test plants a digest that
no real installation would produce" — they do not confer authority by
existing, and this module cannot make them do so: it performs no I/O, so it
has no way to confirm a `RowCountSnapshotV1` was actually written by a backup
step it did not also invoke, or that a `RowCountObserver`'s counts came from a
disposable target rather than a literal the SAME caller also typed into the
snapshot. **A single caller that constructs both a snapshot and an observer
from matching, self-authored numbers still produces evidence this module
calls `ok`** — proven, not asserted, by
`test_a_single_caller_controlling_both_sides_still_admits` below, kept
permanently as the negative control this finding demands.

Closing that gap for real needs one of two things this module cannot supply
by itself: either the snapshot is bound to a `backup.BackupRecord` written by
an audited backup pipeline the verifying caller does not control (a larger
integration into `backup.py`, and a decision about that module's shape that
is not this contract's to make unilaterally), or the observer is the
DISCOVERED entry point from finding 1's fix, loaded and invoked by a caller
that is itself not the one holding the expected numbers (engine wiring,
embargoed). Element 1's fix narrows the gap — the observer identity must be
independently INSTALLED, not merely invented at the call site — but
installation is not invocation, and a test double can still be registered.
This module's honest claim is: correct arithmetic over what it is given, with
full provenance recorded for later dispute; independence of who produced each
side is a property of the caller this module does not and cannot enforce
alone.

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

3. **Declared expectation source** — `RowCountExpectationSource` names the
   KIND of source (a closed vocabulary, exactly like `BackupDataset.KINDS`),
   and `RowCountSnapshotV1` names the PARTICULAR one — see "Revision" above.

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
   expected count, observed count, and the tolerance that was applied, plus
   the snapshot identity and both timestamps (element 4 above), so a run that
   passed and a run that never compared read differently to whatever reads
   the evidence afterward.

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
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any, Final, Protocol

from .discovery import declared_names
from .errors import PreconditionFailed, SpecError

__all__ = [
    "ROW_COUNT_EXPECTATION_INCOMPLETE",
    "ROW_COUNT_OBSERVATION_INCOMPLETE",
    "ROW_COUNT_OBSERVER_ABSENT",
    "ROW_COUNT_OBSERVER_ENTRY_POINT_GROUP",
    "ROW_COUNT_OBSERVER_NOT_DECLARED",
    "ROW_COUNT_OUT_OF_TOLERANCE",
    "ROW_COUNT_SNAPSHOT_DATASET_MISMATCH",
    "ROW_COUNT_TARGET_UNDECLARED",
    "RowCountEvidence",
    "RowCountExpectationSource",
    "RowCountFinding",
    "RowCountObserver",
    "RowCountObserverIdentity",
    "RowCountSnapshotV1",
    "RowCountToleranceV1",
    "RowCountVerificationSpec",
    "declared_row_count_observer_names",
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
ROW_COUNT_OBSERVER_NOT_DECLARED: Final = "row_counts.observer_not_declared"
ROW_COUNT_SNAPSHOT_DATASET_MISMATCH: Final = "row_counts.snapshot_dataset_mismatch"

#: The entry-point group a dataset's declared `observer.identifier` must
#: appear in for `spec.py` to admit `row_count_verification` at parse. Named
#: after, and discovered through, the same core `execution_bindings
#: .ENTRY_POINT_GROUP` uses (`discovery.declared_names` — metadata only, no
#: import, safe on `validate`). A distinct group rather than reusing
#: `execution_bindings`'s: an execution-bindings provider and a row-count
#: observer answer different questions, and one installed distribution
#: legitimately declaring only one of them must not satisfy the other.
ROW_COUNT_OBSERVER_ENTRY_POINT_GROUP: Final = (
    "dotmac_deployment_foundation.row_count_observers"
)


def declared_row_count_observer_names(
    *, entries: Iterable[Any] | None = None
) -> tuple[str, ...]:
    """Every declared row-count observer name, WITHOUT importing any of it.

    Thin wrapper over `discovery.declared_names`, not a reimplementation —
    that module's own docstring is why a second copy of "locate the one
    declared thing of a kind" is a second authority over one question rather
    than a convenience. ``entries`` exists for the identical reason
    `execution_bindings.declared_provider_names` takes it: a test plants a
    fake entry-point listing without installing a real distribution.
    """
    return declared_names(ROW_COUNT_OBSERVER_ENTRY_POINT_GROUP, entries=entries)


class RowCountExpectationSource(str, Enum):
    """Where the expected count comes from — declared, never inferred.

    One member today, deliberately closed rather than left open: a descriptor
    naming a source this module does not recognise is refused at parse
    (`spec.py`), not silently treated as "trust the caller's number".

    ``SOURCE_SNAPSHOT``: the expected count is the count this dataset's own
    backup snapshot recorded for the table at capture time — the same
    "captured, then compared against a restore" shape `recovery.py` already
    uses for schemas and migration heads. It names the KIND of source; a
    PARTICULAR snapshot is :class:`RowCountSnapshotV1`, supplied by the caller
    to :func:`verify_row_counts`. This module does not read a snapshot itself,
    because that would make it stateful.
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
class RowCountObserverIdentity:
    """WHO performs the internal observation — named, like `ExternalExecutorV1`.

    No `key_id`: this is not a signed receipt from a third party, it is a
    claim about which installed entry point this deployment's environment is
    supposed to carry. `identifier` must equal an entry point name in
    :data:`ROW_COUNT_OBSERVER_ENTRY_POINT_GROUP` — checked at parse
    (`spec.py`) via metadata only, never by importing it.
    """

    identifier: str
    version: str

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise SpecError(
                "row_count_verification.observer.identifier must not be blank "
                "— an anonymous performer cannot be checked against anything "
                "installed"
            )
        if not self.version.strip():
            raise SpecError(
                "row_count_verification.observer.version must not be blank — "
                "an unversioned performer's evidence cannot be told apart from "
                "a different procedure that happens to share a name"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountVerificationSpec:
    """The declared internal contract for a dataset's `row_counts` check.

    An alternative to naming an `[backup.datasets.external_executor]`: a
    dataset that declares this block instead is asking THIS package to observe
    the counts itself (via a caller-supplied :class:`RowCountObserver`) rather
    than trusting a signed receipt from elsewhere. `spec.py` accepts either —
    never both (`ROW_COUNT_DUAL_PERFORMER` — two performers for one
    verification is an ambiguous authority) and never neither, since
    `row_counts` with nobody declared to perform it is still refused at parse.
    """

    tables: tuple[str, ...]
    expectation_source: RowCountExpectationSource
    tolerance: RowCountToleranceV1
    observer: RowCountObserverIdentity


@dataclasses.dataclass(frozen=True, slots=True)
class RowCountSnapshotV1:
    """A PARTICULAR captured snapshot's row counts — element 4 and finding 4.

    `RowCountExpectationSource` names the KIND of source; this names the ONE
    snapshot the expected counts in ``counts`` came from, so a verdict can be
    re-derived and disputed afterward: which snapshot, captured when, for
    which dataset. Bound to ``dataset`` at `verify_row_counts` time
    (`ROW_COUNT_SNAPSHOT_DATASET_MISMATCH`) so a snapshot captured for one
    dataset cannot silently stand in for another's expectation.

    Freely constructible, like `host_source.CandidateReceipt` and
    `backup.BackupRecord` before it — see the module docstring's "What this
    module does NOT prove" for why that is a stated, accepted limit rather
    than an oversight.
    """

    dataset: str
    snapshot_id: str
    captured_at_epoch: int
    counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.dataset.strip():
            raise SpecError("a row-count snapshot must name its `dataset`")
        if not self.snapshot_id.strip():
            raise SpecError(
                "a row-count snapshot must carry a non-blank `snapshot_id` — an "
                "anonymous snapshot cannot be the thing a later dispute points "
                "at"
            )
        if self.captured_at_epoch < 0:
            raise SpecError("a row-count snapshot's `captured_at_epoch` must be >= 0")


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
    evidence is itself a pass. Carries full snapshot/target provenance
    (finding 4): which snapshot, captured when, which disposable target,
    observed when — so a verdict can be re-derived rather than merely
    reported. See the module docstring's "What this module does NOT prove"
    for the limit of what this record establishes.
    """

    dataset: str
    restore_target: str
    expectation_source: RowCountExpectationSource
    snapshot_id: str
    snapshot_captured_at_epoch: int
    observed_at_epoch: int
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
    snapshot: RowCountSnapshotV1,
    restore_target: str,
    observer: RowCountObserver | None,
    now_epoch: int,
) -> RowCountEvidence:
    """Observe ``verification.tables`` on ``restore_target`` and compare.

    Refuses — raises, never returns a false pass and never returns silently —
    when the target is undeclared, when the snapshot names a different
    dataset, when no observer is installed, or when the observer cannot
    produce a count for every declared table. This is element 6: an
    unobservable verification says so, in a form CI can assert both the
    exception class and the refusal identity of.

    ``now_epoch`` is the caller's clock reading for the observation, taken the
    same way `external_recovery.require_restore_proof` takes one — supplied
    rather than read internally, so this stays a pure function of its inputs.
    """
    if snapshot.dataset != dataset_code:
        raise SpecError(
            f"the row-count snapshot names dataset {snapshot.dataset!r}, not "
            f"{dataset_code!r}. A snapshot captured for one dataset does not "
            "state an expectation for another, even if a table name happens "
            "to match",
            code=ROW_COUNT_SNAPSHOT_DATASET_MISMATCH,
        )
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
    missing_expected = sorted(set(verification.tables) - set(snapshot.counts))
    if missing_expected:
        raise SpecError(
            f"dataset {dataset_code!r} declares row_count_verification tables "
            f"{missing_expected} for which snapshot {snapshot.snapshot_id!r} "
            f"supplies no count ({verification.expectation_source.value}). A "
            "table with no declared expectation cannot be compared, only "
            "asserted",
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
            expected=snapshot.counts[table],
            observed=observed[table],
            allowed_delta=verification.tolerance.allowed_delta(snapshot.counts[table]),
        )
        for table in verification.tables
    )
    return RowCountEvidence(
        dataset=dataset_code,
        restore_target=restore_target,
        expectation_source=verification.expectation_source,
        snapshot_id=snapshot.snapshot_id,
        snapshot_captured_at_epoch=snapshot.captured_at_epoch,
        observed_at_epoch=now_epoch,
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
            f"target {evidence.restore_target!r} (snapshot "
            f"{evidence.snapshot_id!r} captured at "
            f"{evidence.snapshot_captured_at_epoch}, observed at "
            f"{evidence.observed_at_epoch}) found {len(breaches)} table(s) "
            f"outside their declared tolerance: {detail}",
            code=ROW_COUNT_OUT_OF_TOLERANCE,
        )
    return (
        f"dataset {evidence.dataset!r} row_counts verification on "
        f"{evidence.restore_target!r}: {len(evidence.findings)} table(s) within "
        f"tolerance ({evidence.expectation_source.value}, snapshot "
        f"{evidence.snapshot_id!r} captured at "
        f"{evidence.snapshot_captured_at_epoch}, observed at "
        f"{evidence.observed_at_epoch})"
    )
