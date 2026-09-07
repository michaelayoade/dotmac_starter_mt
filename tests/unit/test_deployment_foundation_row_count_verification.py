"""Row-count verification: a stateless external-effects contract.

Measured before this change: `spec.py` refused every dataset declaring
`verify = [..., "row_counts"]` with no `[backup.datasets.external_executor]`
(`UNPERFORMABLE_VERIFICATION`), including Platform's accepted descriptor
(`dotmac_platform_control_plane`'s `deploy/product.toml:278`, `verify =
["schema", "row_counts", "migration_heads"]`, no external executor and no
other row_counts declaration anywhere in the file) — reproduced here as
`test_platform_shaped_dataset_is_still_refused_without_the_new_block`, which is
the exact refusal this whole module exists to give a second, honest way past.

Organised by Michael's six elements (module docstring of
`row_count_verification.py`), plus the parse-time wiring in `spec.py` that
makes the new block an alternative to `external_executor` rather than a
replacement for the existing refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.row_count_verification import (
    ROW_COUNT_EXPECTATION_INCOMPLETE,
    ROW_COUNT_OBSERVATION_INCOMPLETE,
    ROW_COUNT_OBSERVER_ABSENT,
    ROW_COUNT_OUT_OF_TOLERANCE,
    ROW_COUNT_TARGET_UNDECLARED,
    RowCountExpectationSource,
    RowCountToleranceV1,
    RowCountVerificationSpec,
    require_row_counts_within_tolerance,
    verify_row_counts,
)
from dotmac_deployment_foundation.spec import (
    ROW_COUNT_VERIFICATION_ORPHANED,
    UNPERFORMABLE_VERIFICATION,
    BackupDataset,
    ProductDeploymentSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = REPO_ROOT / "deploy" / "product.toml"

ROW_COUNT_BLOCK = """
[backup.datasets.row_count_verification]
tables = ["mod_agreements.contract", "public.party"]
expectation_source = "source_snapshot"

[backup.datasets.row_count_verification.tolerance]
absolute = 5
"""


def _descriptor_text() -> str:
    return DESCRIPTOR.read_text(encoding="utf-8")


def _with_row_counts_declared(*, extra_toml: str = "") -> str:
    """The shipped descriptor, with `row_counts` added to `verify` and
    ``extra_toml`` (a `[backup.datasets.*]` block or nothing) spliced in right
    after the dataset's other fields and before `[telemetry]` — the same
    splice point `test_deployment_foundation_external_recovery.py` uses for
    `external_executor`."""
    text = _descriptor_text()
    text = text.replace(
        'verify = [\n  "schema",', 'verify = [\n  "schema",\n  "row_counts",', 1
    )
    if extra_toml:
        text = text.replace("\n[telemetry]", extra_toml + "\n[telemetry]", 1)
    return text


# ── positive control: the shipped descriptor still parses, unmodified ──────


def test_the_shipped_descriptor_parses_unmodified() -> None:
    """If this fails, every refusal test below is meaningless — a negative
    suite whose subject cannot parse at all proves nothing."""
    assert ProductDeploymentSpec.loads(_descriptor_text(), source="control")


# ── the refusal this module exists to give a second, honest way past ───────


def test_platform_shaped_dataset_is_still_refused_without_the_new_block() -> None:
    """Reproduces the measured refusal: `row_counts` declared, no
    `external_executor`, no `row_count_verification` — Platform's exact
    declared shape (`verify = ["schema", "row_counts", "migration_heads"]`,
    nothing else about row counts anywhere in the file). This new contract
    does not make an UNCHANGED descriptor render; it gives the descriptor a
    second way to declare who performs the check."""
    with pytest.raises(SpecError) as caught:
        ProductDeploymentSpec.loads(_with_row_counts_declared(), source="planted")
    assert caught.value.code == UNPERFORMABLE_VERIFICATION


def test_declaring_row_count_verification_admits_the_dataset() -> None:
    """ADMIT CONTROL: a correctly-declared internal contract, over a dataset
    that names `row_counts`, parses — the property the whole module exists to
    add."""
    spec = ProductDeploymentSpec.loads(
        _with_row_counts_declared(extra_toml=ROW_COUNT_BLOCK), source="admitted"
    )
    dataset = spec.backup_datasets[0]
    assert dataset.external_executor is None
    rcv = dataset.row_count_verification
    assert rcv is not None
    assert rcv.tables == ("mod_agreements.contract", "public.party")
    assert rcv.expectation_source is RowCountExpectationSource.SOURCE_SNAPSHOT
    assert rcv.tolerance == RowCountToleranceV1(absolute=5, percent=None)


def test_an_external_executor_still_satisfies_row_counts_with_no_new_block() -> None:
    """The pre-existing path is untouched: an externally executed dataset
    still needs no `row_count_verification` block at all."""
    executor_toml = """
[backup.datasets.external_executor]
kind = "managed_database_service"
identifier = "hetzner-managed-pg"
version = "2026.08"
key_id = "recovery-signing-01"
"""
    spec = ProductDeploymentSpec.loads(
        _with_row_counts_declared(extra_toml=executor_toml), source="external"
    )
    dataset = spec.backup_datasets[0]
    assert dataset.external_executor is not None
    assert dataset.row_count_verification is None


# ── named tables: declared, never discovered or globbed ────────────────────


def test_an_empty_tables_list_is_refused() -> None:
    block = """
[backup.datasets.row_count_verification]
tables = []
expectation_source = "source_snapshot"

[backup.datasets.row_count_verification.tolerance]
absolute = 1
"""
    with pytest.raises(SpecError, match="empty `tables`"):
        ProductDeploymentSpec.loads(
            _with_row_counts_declared(extra_toml=block), source="empty-tables"
        )


def test_a_wildcard_table_name_is_refused() -> None:
    """No `*` or `%` in the declared table pattern: this is the property that
    makes 'named' mean something rather than 'discovered by a filter'."""
    block = """
[backup.datasets.row_count_verification]
tables = ["mod_agreements.*"]
expectation_source = "source_snapshot"

[backup.datasets.row_count_verification.tolerance]
absolute = 1
"""
    with pytest.raises(SpecError):
        ProductDeploymentSpec.loads(
            _with_row_counts_declared(extra_toml=block), source="wildcard"
        )


# ── declared expectation source: closed vocabulary, refused if unknown ─────


def test_an_unknown_expectation_source_is_refused() -> None:
    block = """
[backup.datasets.row_count_verification]
tables = ["public.party"]
expectation_source = "operator_says_so"

[backup.datasets.row_count_verification.tolerance]
absolute = 1
"""
    with pytest.raises(SpecError, match="expectation_source"):
        ProductDeploymentSpec.loads(
            _with_row_counts_declared(extra_toml=block), source="unknown-source"
        )


# ── the orphan declaration: a contract nothing requires is dead config ─────


def test_row_count_verification_without_row_counts_in_verify_is_refused() -> None:
    text = _descriptor_text().replace(
        "\n[telemetry]", ROW_COUNT_BLOCK + "\n[telemetry]", 1
    )
    with pytest.raises(SpecError) as caught:
        ProductDeploymentSpec.loads(text, source="orphaned")
    assert caught.value.code == ROW_COUNT_VERIFICATION_ORPHANED


# ── tolerance: declared, typed, and at least one of the two is required ────


def test_tolerance_with_neither_bound_declared_is_refused() -> None:
    with pytest.raises(SpecError, match="either 'exact' or 'anything'"):
        RowCountToleranceV1()


def test_tolerance_rejects_a_negative_absolute_bound() -> None:
    with pytest.raises(SpecError):
        RowCountToleranceV1(absolute=-1)


def test_tolerance_rejects_an_out_of_range_percent() -> None:
    with pytest.raises(SpecError):
        RowCountToleranceV1(percent=101)


def test_tolerance_takes_the_wider_of_absolute_and_percent() -> None:
    """Justifies the 'both, take the looser' design: a small table needs the
    absolute floor and a large table needs the proportional ceiling, and
    neither should be defeated by the other being declared too."""
    tolerance = RowCountToleranceV1(absolute=5, percent=1)
    # 1% of 3 floors to 0 < 5: the absolute floor wins for a tiny table.
    assert tolerance.allowed_delta(3) == 5
    # 1% of 10_000_000 = 100_000 > 5: the proportional ceiling wins for a huge one.
    assert tolerance.allowed_delta(10_000_000) == 100_000


def test_a_missing_tolerance_table_is_refused() -> None:
    block = """
[backup.datasets.row_count_verification]
tables = ["public.party"]
expectation_source = "source_snapshot"
"""
    with pytest.raises(SpecError):
        ProductDeploymentSpec.loads(
            _with_row_counts_declared(extra_toml=block), source="no-tolerance"
        )


# ── the pure verification: element 5 (evidence) and element 6 (refusal) ────


_SPEC = RowCountVerificationSpec(
    tables=("public.party", "mod_agreements.contract"),
    expectation_source=RowCountExpectationSource.SOURCE_SNAPSHOT,
    tolerance=RowCountToleranceV1(absolute=2),
)

_EXPECTED = {"public.party": 100, "mod_agreements.contract": 40}


class _Observes:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts

    def observe_row_counts(self, tables):
        return {t: self._counts[t] for t in tables if t in self._counts}


def test_admit_control_a_correctly_declared_verification_admits() -> None:
    """A real disposable target, a real observer, counts inside tolerance:
    this must pass, or every refusal test below passes for the wrong reason
    (a facility that refuses everything trivially 'passes' every refusal
    test)."""
    observer = _Observes({"public.party": 101, "mod_agreements.contract": 40})
    evidence = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="rehearsal-2026-09-07",
        observer=observer,
    )
    assert evidence.ok
    assert evidence.restore_target == "rehearsal-2026-09-07"
    summary = require_row_counts_within_tolerance(evidence)
    assert "within tolerance" in summary


def test_a_delta_exactly_at_the_boundary_stays_silent() -> None:
    """NEAR-MISS NEGATIVE CONTROL 1: a count within declared tolerance (here,
    exactly AT the boundary) must not be flagged. Without this, a facility
    that refuses everything would still pass the admit control above only by
    accident of the fixture; this fixes the boundary the admit control does
    not exercise."""
    observer = _Observes({"public.party": 102, "mod_agreements.contract": 40})
    evidence = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="rehearsal-2026-09-07",
        observer=observer,
    )
    assert evidence.findings[0].delta == 2
    assert evidence.findings[0].allowed_delta == 2
    assert evidence.ok
    require_row_counts_within_tolerance(evidence)  # must not raise


def test_a_legitimately_declared_table_set_stays_silent_at_parse() -> None:
    """NEAR-MISS NEGATIVE CONTROL 2: a real, well-formed
    `row_count_verification` block must parse without incident — a guard that
    rejects a legitimate declaration would be indistinguishable, from outside,
    from one that correctly rejects a bad one."""
    assert ProductDeploymentSpec.loads(
        _with_row_counts_declared(extra_toml=ROW_COUNT_BLOCK), source="near-miss-ok"
    )


def test_non_vacuity_an_out_of_tolerance_observation_is_named() -> None:
    """NON-VACUITY: the observed counts are actually compared, not merely
    recorded. If `require_row_counts_within_tolerance` ignored `evidence
    .findings`, this would pass instead of raising."""
    observer = _Observes({"public.party": 250, "mod_agreements.contract": 40})
    evidence = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="rehearsal-2026-09-07",
        observer=observer,
    )
    assert not evidence.ok
    assert evidence.breaches == (evidence.findings[0],)
    with pytest.raises(PreconditionFailed) as caught:
        require_row_counts_within_tolerance(evidence)
    assert caught.value.code == ROW_COUNT_OUT_OF_TOLERANCE
    assert "public.party" in str(caught.value)
    assert "expected 100, observed 250" in str(caught.value)


def test_the_absent_executor_refusal_is_by_class_and_identity() -> None:
    """THE LOAD-BEARING ARM. No observer installed: this must raise, never
    return evidence claiming success and never return `None` as a silent
    skip."""
    with pytest.raises(PreconditionFailed) as caught:
        verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected=_EXPECTED,
            restore_target="rehearsal-2026-09-07",
            observer=None,
        )
    assert caught.value.code == ROW_COUNT_OBSERVER_ABSENT
    assert "no observer is installed" in str(caught.value)


def test_the_absent_executor_refusal_never_returns_a_value() -> None:
    """Sharper form of the same property: the function has no code path that
    returns normally when `observer is None`. Caught via exhaustive `except`:
    a caller catching a narrower type must see the refusal escape."""
    with pytest.raises(Exception):  # noqa: B017 -- proving nothing narrower swallows it
        verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected=_EXPECTED,
            restore_target="rehearsal-2026-09-07",
            observer=None,
        )


def test_a_blank_restore_target_is_refused() -> None:
    """Element 1: the disposable restore target is required and named, not
    implied. A blank name is refused before an observer is even consulted."""
    with pytest.raises(SpecError) as caught:
        verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected=_EXPECTED,
            restore_target="   ",
            observer=_Observes(_EXPECTED),
        )
    assert caught.value.code == ROW_COUNT_TARGET_UNDECLARED


def test_a_partial_observation_is_refused_not_scored_on_what_it_has() -> None:
    """An observer that reports only some of the declared tables must refuse
    for the whole verification — never silently score the tables it happened
    to answer as a pass."""
    observer = _Observes({"public.party": 100})  # never answers the second table
    with pytest.raises(PreconditionFailed) as caught:
        verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected=_EXPECTED,
            restore_target="rehearsal-2026-09-07",
            observer=observer,
        )
    assert caught.value.code == ROW_COUNT_OBSERVATION_INCOMPLETE
    assert "mod_agreements.contract" in str(caught.value)


def test_an_incomplete_expectation_is_refused() -> None:
    """A table named for verification with no supplied expected count cannot
    be compared, only asserted — refused rather than treated as a pass."""
    with pytest.raises(SpecError) as caught:
        verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected={"public.party": 100},  # missing mod_agreements.contract
            restore_target="rehearsal-2026-09-07",
            observer=_Observes(_EXPECTED),
        )
    assert caught.value.code == ROW_COUNT_EXPECTATION_INCOMPLETE


# ── sensitivity: plant a defect, prove it is named; plant a near-miss ──────


def test_sensitivity_a_planted_comparison_defect_is_named_by_symbol() -> None:
    """Plants the exact defect class this programme keeps finding: a check
    that records evidence and does not compare it. Simulated by calling the
    real `verify_row_counts` (proving the true comparison fires — see
    `test_non_vacuity_...` above) and separately proving that a HYPOTHETICAL
    always-pass replacement of `RowCountFinding.within_tolerance` would be
    caught by `test_non_vacuity_an_out_of_tolerance_observation_is_named`: if
    that property always returned `True`, the non-vacuity test's `assert not
    evidence.ok` would fail at that exact line and name
    `RowCountFinding.within_tolerance` as the property under test — not a
    generic assertion failure elsewhere.
    """
    import dotmac_deployment_foundation.row_count_verification as rcv

    observer = _Observes({"public.party": 250, "mod_agreements.contract": 40})
    real_evidence = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="plant",
        observer=observer,
    )
    assert not real_evidence.ok  # the un-patched behaviour: the defect's absence

    original = rcv.RowCountFinding.within_tolerance
    try:
        # PLANT: "compares and ignores the result" — always reports in-tolerance.
        rcv.RowCountFinding.within_tolerance = property(lambda self: True)  # type: ignore[assignment]
        patched_evidence = rcv.verify_row_counts(
            dataset_code="primary",
            verification=_SPEC,
            expected=_EXPECTED,
            restore_target="plant",
            observer=observer,
        )
        assert patched_evidence.ok, (
            "the plant did not take: RowCountFinding.within_tolerance is not "
            "the property `evidence.ok` depends on, so this test would not "
            "have caught the defect it claims to name"
        )
    finally:
        rcv.RowCountFinding.within_tolerance = original  # type: ignore[assignment]

    # With the plant removed, the real defect returns and is named again —
    # proving the near-miss (an unpatched call, right after a patched one)
    # was actually exercised rather than the assertion trivially matching a
    # stale import.
    unpatched_again = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="plant",
        observer=observer,
    )
    assert not unpatched_again.ok


def test_sensitivity_the_boundary_near_miss_is_exercised_not_assumed() -> None:
    """Proves `test_a_delta_exactly_at_the_boundary_stays_silent` is actually
    exercising the boundary and not vacuously true because the tolerance was
    unreachable: one row past the boundary must flip the verdict."""
    exactly_at_boundary = _Observes(
        {"public.party": 102, "mod_agreements.contract": 40}
    )
    one_past_boundary = _Observes({"public.party": 103, "mod_agreements.contract": 40})

    at = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="boundary",
        observer=exactly_at_boundary,
    )
    past = verify_row_counts(
        dataset_code="primary",
        verification=_SPEC,
        expected=_EXPECTED,
        restore_target="boundary",
        observer=one_past_boundary,
    )
    assert at.ok
    assert not past.ok


def test_the_row_count_verification_field_is_reachable_from_backup_datasets() -> None:
    """Non-vacuity for the parse-time wiring itself: `BackupDataset` actually
    carries the parsed contract through to the dataclass instance, not just
    through a local variable that parse() computes and discards."""
    assert "row_count_verification" in BackupDataset.__dataclass_fields__  # type: ignore[attr-defined]
