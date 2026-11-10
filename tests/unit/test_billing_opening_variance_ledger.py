"""ST-4c opening variance ledger: pure closure, citation and cash-lot rules.

Every equation is exact integer arithmetic; a single minor unit refuses. The
fixtures derive exact (zero-variance) measures from the manifest itself, so no
assertion is weakened to make a fixture pass.
"""

from __future__ import annotations

from dataclasses import fields, replace
from uuid import uuid4

import pytest
from dotmac_billing.opening_contracts import (
    OpeningDispositionV1,
    OpeningEvidenceError,
    OpeningLaneControlsV1,
    OpeningPopulationCountsV1,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_receivable_validation import (
    opening_manifest_fingerprint_v2,
    opening_manifest_variance_blockers,
    require_hold_list_covers_manifest,
)
from dotmac_billing.opening_variance_contracts import (
    OpeningBatchControlV1,
    OpeningOperationalHoldV1,
    OpeningVarianceV1,
    is_customer_adverse,
    reason_policy,
)
from dotmac_billing.opening_variance_validation import (
    HISTORY_FIELDS,
    VARIANCE_GOVERNED_LANE_FIELDS,
)

from tests import billing_opening_variance_fixtures as variance_fixture
from tests.unit import test_billing_opening_receivable_contracts as ar

DIGEST = variance_fixture.DIGEST
RULES = variance_fixture.RULES


def _base() -> OpeningManifestV2:
    return variance_fixture.with_exact_measures(ar._candidate().manifest)


def _codes(manifest: OpeningManifestV2) -> set[str]:
    return {b.code for b in opening_manifest_variance_blockers(manifest)}


def _refresh(manifest: OpeningManifestV2) -> OpeningManifestV2:
    return variance_fixture.with_exact_measures(manifest)


def _variance(
    manifest: OpeningManifestV2,
    *,
    reason: str,
    basis: str,
    kind: str,
    key: str | None,
    amount: int,
    lane: str = "receivable",
    level: str = "partition",
    currency: str = "NGN",
) -> OpeningVarianceV1:
    part = manifest.controls[0].partition
    return OpeningVarianceV1(
        level,
        None if level == "batch" else part,
        currency,
        part.precision,
        lane,
        basis,
        reason,
        OpeningSourceIdentityV1(kind, part.source_account_key if key is None else key),
        amount,
        DIGEST,
        RULES,
    )


def _with_control(manifest: OpeningManifestV2, **changes: int) -> OpeningManifestV2:
    return replace(manifest, controls=(replace(manifest.controls[0], **changes),))


def _with_measure(manifest: OpeningManifestV2, **changes: int) -> OpeningManifestV2:
    return replace(manifest, measures=(replace(manifest.measures[0], **changes),))


def test_the_exact_fixture_closes_every_equation() -> None:
    assert _codes(_base()) == set()


# -- Q7: an a10-shaped manifest is refused ------------------------------------


def test_an_a10_shaped_manifest_without_measures_is_refused_and_re_fingerprints() -> (
    None
):
    manifest = _base()
    a10_shaped = replace(manifest, measures=())
    assert "partition_measures_missing" in _codes(a10_shaped)
    assert opening_manifest_fingerprint_v2(a10_shaped) != (
        opening_manifest_fingerprint_v2(manifest)
    )


# -- closure fails by one minor unit, per equation ------------------------------


def test_each_equation_refuses_a_single_minor_unit() -> None:
    manifest = _base()
    total = manifest.controls[0].collectible_receivable_minor
    lane = _with_control(
        manifest, collectible_receivable_minor=total + 1, open_ar_minor=total + 1
    )
    assert _codes(lane) == {"lane_control_unclosed"}
    doc = _with_measure(
        manifest,
        document_residual_minor=manifest.measures[0].document_residual_minor + 1,
    )
    assert _codes(doc) == {"document_control_unclosed"}
    stored = _with_measure(
        manifest, stored_balance_minor=manifest.measures[0].stored_balance_minor + 1
    )
    assert _codes(stored) == {"stored_balance_control_unclosed"}
    rebuild = _with_measure(
        manifest, rebuild_minor=(manifest.measures[0].rebuild_minor or 0) + 1
    )
    assert _codes(rebuild) == {"rebuild_control_unclosed"}


def test_a_matching_variance_closes_the_difference_exactly() -> None:
    manifest = _base()
    total = manifest.controls[0].collectible_receivable_minor
    carried = replace(
        _with_control(
            manifest,
            collectible_receivable_minor=total - 700,
            open_ar_minor=total - 700,
        ),
        variances=(
            _variance(
                manifest,
                reason="lane_document_difference",
                basis="ledger_lane",
                kind="lane",
                key=None,
                amount=-700,
            ),
        ),
    )
    assert _codes(carried) == set()
    off_by_one = replace(
        carried,
        variances=(replace(carried.variances[0], amount_minor=-701),),
    )
    assert _codes(off_by_one) == {"lane_control_unclosed"}


# -- Finding 5: the closure has no hole ------------------------------------------


def test_a_control_partition_with_a_figure_but_no_t_and_no_variance_is_not_silent() -> (
    None
):
    manifest = _base()
    stranger = replace(
        manifest.controls[0].partition,
        source_account_key="orphan",
        account_id=uuid4(),
    )
    orphan = OpeningLaneControlsV1(stranger, 500, 0, 0, 0, 0, 0, 0, 0, 0, 500)
    widened = replace(
        manifest,
        controls=(*manifest.controls, orphan),
        measures=(
            *manifest.measures,
            replace(
                manifest.measures[0],
                partition=stranger,
                document_residual_minor=0,
                stored_balance_minor=0,
                rebuild_minor=0,
            ),
        ),
    )
    codes = _codes(widened)
    assert "lane_control_unclosed" in codes
    refs = {
        ref for b in opening_manifest_variance_blockers(widened) for ref in b.references
    }
    assert "orphan:receivable" in refs


# -- Q4: flow fields are history; T_net; the cash-lot rule ---------------------------


def test_cumulative_flow_fields_are_history_with_no_equation() -> None:
    manifest = _with_control(
        _base(),
        prepaid_funding_consumed_minor=11,
        written_off_total_minor=22,
        refunded_total_minor=33,
        adjustment_total_minor=44,
    )
    assert _codes(manifest) == set()


def test_non_cash_credit_still_must_be_zero() -> None:
    manifest = _with_control(_base(), available_non_cash_credit_minor=1)
    assert "unsupported_lane" in _codes(manifest)


def test_the_history_set_and_the_governed_set_cover_the_controls_exactly() -> None:
    names = {f.name for f in fields(OpeningLaneControlsV1)} - {"partition"}
    cash = {"available_cash_credit_minor"}
    non_cash = {"available_non_cash_credit_minor"}
    governed = set(VARIANCE_GOVERNED_LANE_FIELDS) - set(HISTORY_FIELDS)
    assert names == governed | set(HISTORY_FIELDS) | cash | non_cash
    assert not governed & set(HISTORY_FIELDS)


def test_a_lot_partition_with_unapplied_credit_is_refused() -> None:
    manifest = _with_control(_base(), unapplied_customer_credit_minor=1)
    assert "credit_transfer_unsupported" in _codes(manifest)


def test_a_lot_partition_with_reserved_funding_is_refused() -> None:
    manifest = _with_control(_base(), prepaid_funding_reserved_minor=1)
    assert "credit_transfer_unsupported" in _codes(manifest)


@pytest.mark.parametrize("lane", ["customer_credit", "prepaid_funding"])
def test_a_lot_partition_with_a_credit_or_funding_variance_is_refused(
    lane: str,
) -> None:
    manifest = _base()
    field = (
        "unapplied_customer_credit_minor"
        if lane == "customer_credit"
        else "prepaid_funding_reserved_minor"
    )
    carried = replace(
        _with_control(manifest, **{field: 40}),
        variances=(
            _variance(
                manifest,
                reason=f"lane_not_transferred:{lane}",
                basis="ledger_lane",
                kind="lane",
                key=None,
                amount=40,
                lane=lane,
            ),
        ),
    )
    # E1 closes (40 = 0 + 40); the double-count refusal is what stops it.
    assert _codes(carried) == {"credit_transfer_unsupported"}


def test_a_lots_only_partition_closes_every_row() -> None:
    manifest = _base()
    items = {i.source_identity for i in manifest.receivables}
    kept = tuple(d for d in manifest.dispositions if d.identity not in items)
    lots_only = _refresh(
        replace(
            manifest,
            receivables=(),
            population=tuple(d.identity for d in kept),
            dispositions=kept,
            counts=OpeningPopulationCountsV1(len(kept), len(kept), 0, 0),
            controls=(
                replace(
                    manifest.controls[0],
                    collectible_receivable_minor=0,
                    open_ar_minor=0,
                ),
            ),
        )
    )
    assert lots_only.measures[0].document_residual_minor == 0
    assert lots_only.measures[0].rebuild_minor == -6000  # T_rec - sum(lots)
    assert _codes(lots_only) == set()


def test_e4_on_a_mixed_lot_and_receivable_partition_closes_with_the_netted_t() -> None:
    manifest = _base()
    measure = manifest.measures[0]
    assert measure.rebuild_minor == measure.document_residual_minor - 6000
    assert _codes(manifest) == set()
    un_netted = _with_measure(manifest, rebuild_minor=measure.document_residual_minor)
    assert _codes(un_netted) == {"rebuild_control_unclosed"}


# -- carried rows and the count control ---------------------------------------------


def _with_excluded_residue(manifest: OpeningManifestV2) -> OpeningManifestV2:
    part = manifest.controls[0].partition
    identity = OpeningSourceIdentityV1("invoice", "invoice:closed")
    row = OpeningDispositionV1(identity, part, "excluded", "closed", 300)
    widened = replace(
        manifest,
        population=(*manifest.population, identity),
        dispositions=(*manifest.dispositions, row),
        counts=OpeningPopulationCountsV1(5, 4, 1, 0),
        variances=(
            _variance(
                manifest,
                reason="closed_invoice_residue",
                basis="document_residual",
                kind="invoice",
                key="invoice:closed",
                amount=300,
            ),
        ),
    )
    refreshed = _refresh(widened)
    measure = replace(
        refreshed.measures[0],
        document_residual_minor=refreshed.measures[0].document_residual_minor + 300,
    )
    return replace(refreshed, measures=(measure,))


def test_an_excluded_row_carried_as_variance_lands_in_the_carried_bucket() -> None:
    manifest = _with_excluded_residue(_base())
    # the helper counted it as excluded; the cited row must be carried instead
    assert "partition_count_mismatch" in _codes(manifest)
    fixed = replace(
        manifest,
        measures=(replace(manifest.measures[0], carried_rows=1, excluded_rows=0),),
    )
    assert _codes(fixed) == set()
    # every census row lands in exactly one bucket
    with pytest.raises(OpeningEvidenceError):
        replace(fixed.measures[0], carried_rows=2)


# -- Q5: exact citation rules --------------------------------------------------------


def test_the_rebuild_basis_is_receivable_lane_only() -> None:
    manifest = _base()
    with pytest.raises(OpeningEvidenceError):
        _variance(
            manifest,
            reason="rebuild_opening_gap_other",
            basis="rebuild",
            kind="rebuild_account",
            key=None,
            amount=5,
            lane="customer_credit",
        )
    assert reason_policy("rebuild_opening_gap_other").lanes == {"receivable"}  # type: ignore[union-attr]


def test_a_partition_aggregate_must_cite_its_own_account_row() -> None:
    manifest = _base()
    foreign = replace(
        manifest,
        variances=(
            _variance(
                manifest,
                reason="lane_document_difference",
                basis="ledger_lane",
                kind="lane",
                key="some-other-account",
                amount=-10,
            ),
        ),
    )
    assert "variance_source_unresolved" in _codes(foreign)


def test_a_rebuild_variance_needs_a_rebuild_figure() -> None:
    manifest = _with_measure(
        _base(), rebuild_minor=None, rebuild_gap_reason="a10_missing"
    )
    cited = replace(
        manifest,
        variances=(
            _variance(
                manifest,
                reason="rebuild_opening_gap_other",
                basis="rebuild",
                kind="rebuild_account",
                key=None,
                amount=5,
            ),
        ),
    )
    assert "variance_source_unresolved" in _codes(cited)


def test_an_itemised_citation_must_be_a_population_row_of_the_partition() -> None:
    manifest = _base()
    ghost = replace(
        manifest,
        variances=(
            _variance(
                manifest,
                reason="stored_balance_difference",
                basis="stored_balance",
                kind="invoice",
                key="invoice:not-in-the-population",
                amount=9,
            ),
        ),
    )
    assert "variance_source_unresolved" in _codes(ghost)


def test_a_duplicate_identity_is_refused() -> None:
    manifest = _base()
    one = _variance(
        manifest,
        reason="stored_balance_difference",
        basis="stored_balance",
        kind="invoice",
        key="invoice:0",
        amount=9,
    )
    assert "variance_duplicate" in _codes(replace(manifest, variances=(one, one)))


def _batch(manifest: OpeningManifestV2, *rows: OpeningSourceIdentityV1):
    return OpeningBatchControlV1("NGN", 2, "receivable", "ledger_lane", 90, rows)


def test_a_batch_payment_must_be_listed_in_exactly_one_matching_control() -> None:
    manifest = _base()
    payment = OpeningSourceIdentityV1("payment", "payment:0")
    variance = _variance(
        manifest,
        reason="unattributed_source",
        basis="ledger_lane",
        kind="payment",
        key="payment:0",
        amount=90,
        level="batch",
    )
    listed = replace(
        manifest,
        variances=(variance,),
        batch_controls=(_batch(manifest, payment),),
    )
    assert _codes(listed) == set()
    unlisted = replace(
        listed, batch_controls=(_batch(manifest, manifest.population[2]),)
    )
    assert "variance_source_unresolved" in _codes(unlisted)
    twice = replace(
        listed,
        batch_controls=(
            _batch(manifest, payment),
            replace(_batch(manifest, payment), basis="document_residual"),
        ),
    )
    assert "variance_source_unresolved" in _codes(twice)


def test_a_batch_control_row_is_the_batch_remainder_keyed_by_its_control() -> None:
    manifest = _base()
    good = _variance(
        manifest,
        reason="unattributed_source",
        basis="ledger_lane",
        kind="control_row",
        key="NGN:receivable:ledger_lane",
        amount=90,
        level="batch",
    )
    closed = replace(manifest, variances=(good,), batch_controls=(_batch(manifest),))
    assert _codes(closed) == set()
    wrong_key = replace(
        closed,
        variances=(replace(good, source=OpeningSourceIdentityV1("control_row", "x")),),
    )
    assert "variance_source_unresolved" in _codes(wrong_key)
    off_by_one = replace(closed, variances=(replace(good, amount_minor=89),))
    assert "batch_control_unclosed" in _codes(off_by_one)


# -- reason policy: source kinds are enforced


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("lane_not_transferred:customer_credit", "invoice"),
        ("stored_balance_difference", "lane"),
        ("unexplained_difference", "invoice"),
        ("draft_allocation", "invoice"),
    ],
)
def test_a_source_kind_not_allowed_for_its_reason_is_refused(
    reason: str, kind: str
) -> None:
    manifest = _base()
    lane = "customer_credit" if "customer_credit" in reason else "receivable"
    basis = "stored_balance" if reason == "stored_balance_difference" else "ledger_lane"
    with pytest.raises(OpeningEvidenceError):
        _variance(
            manifest,
            reason=reason,
            basis=basis,
            kind=kind,
            key="invoice:0",
            amount=1,
            lane=lane,
        )


def test_customer_adverse_signs() -> None:
    assert is_customer_adverse("receivable", -1)
    assert not is_customer_adverse("receivable", 1)
    assert is_customer_adverse("customer_credit", 1)
    assert is_customer_adverse("prepaid_funding", 1)
    assert not is_customer_adverse("customer_credit", -1)


# -- Q1 / finding 9: the sealed hold list equals the typed operational holds ----------


def test_no_blocking_or_marked_disposition_survives_in_a_v2_manifest() -> None:
    manifest = _base()
    marked = tuple(
        replace(d, disposition="blocking", reason="held:fixture")
        for d in manifest.dispositions
    )
    assert "manifest_noncarryable_row" in _codes(replace(manifest, dispositions=marked))


def test_hold_list_equals_operational_holds_exactly() -> None:
    manifest = _base()
    account = manifest.controls[0].partition.account_id
    hold = OpeningOperationalHoldV1(account, "import_refused")
    require_hold_list_covers_manifest(manifest, [account], [hold])
    require_hold_list_covers_manifest(manifest, [], [])
    for listed, reasons in (
        ([account], []),  # listed without a reason
        ([], [hold]),  # a reason for an unlisted partition
        ([account, account], [hold]),  # duplicate
        ([uuid4()], [OpeningOperationalHoldV1(uuid4(), "import_refused")]),
    ):
        with pytest.raises(OpeningEvidenceError):
            require_hold_list_covers_manifest(manifest, listed, reasons)
    stranger = uuid4()
    with pytest.raises(OpeningEvidenceError):  # a non-carryable partition listed
        require_hold_list_covers_manifest(
            manifest, [stranger], [OpeningOperationalHoldV1(stranger, "import_refused")]
        )
    with pytest.raises(OpeningEvidenceError):  # a reason outside the closed set
        OpeningOperationalHoldV1(account, "because")
