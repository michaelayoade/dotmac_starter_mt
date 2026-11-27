"""Unit proofs for the GAP-4 X1 reader: the mapping table, with Billing faked.

No database: the two Billing reads are replaced at the module seam, so each rule
(verified or unknown due date, native or imported exposure, prepaid refusal, the
grace read at the due date) is exercised alone. ``db`` is a bare object.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_billing import (
    BillingRuleViolation,
    DueDateBasisStatus,
    DueDateBasisV1,
    ReceivableExposureV1,
    ReceivableExposureV2,
)
from dotmac_billing.contracts import ServicePeriodEvidenceV1, ServicePeriodStatus
from dotmac_billing.opening_receivable_contracts import OpeningCollectionEvidenceV1
from dotmac_collections import (
    PositionAuthorityMismatch,
    PositionReadOk,
    PositionUnavailable,
    PositionUnknown,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import BillingReceivablesReader, subject_ref_for
from dotmac_money_flows import receivables as x1

NOW = datetime(2026, 11, 1, tzinfo=UTC)
ISSUED = NOW
DUE = NOW + timedelta(days=30)
NGN = Currency("NGN", 2)
SCOPE = TenantScope(uuid4())
ACCOUNT = uuid4()
DOCUMENT = uuid4()


def _basis(*, verified: bool = True, issued=ISSUED) -> DueDateBasisV1:
    if not verified:
        return DueDateBasisV1.unknown_unverified(
            source_authority="dotmac-billing", evidence_ref="missing-terms"
        )
    return DueDateBasisV1(
        status=DueDateBasisStatus.VERIFIED,
        source_authority="dotmac-billing",
        evidence_ref="terms",
        payment_terms_code="net_days",
        payment_terms_version="1",
        issued_at=issued,
        effective_at=issued,
        timezone="Africa/Lagos",
        derivation_policy="issued_at_plus_days",
        derivation_version="1",
    )


def _v1(*, verified: bool = True, state: str = "open", amount: str = "100.00"):
    return ReceivableExposureV1(
        scope=SCOPE,
        source_owner="dotmac-billing",
        exposure_ref=f"document:{DOCUMENT}",
        billing_account_id=ACCOUNT,
        subject_ref="customer:ignored",
        service_ref="service:one",
        collection_timing="arrears",
        source_version=3,
        posting_group_watermark=uuid4(),
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        state_fingerprint="f" * 64,
        observed_at=NOW,
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.NOT_APPLICABLE
        ),
        due_at=DUE if verified else None,
        due_date_basis=_basis(verified=verified),
        financial_state=state,
        collectible_receivable=Money.of(amount, NGN),
    )


def _v2(*, verified: bool = True):
    evidence = OpeningCollectionEvidenceV1(
        status="verified" if verified else "unknown_unverified",
        subject_ref="customer:ignored",
        service_ref="service:two",
        collection_timing="arrears" if verified else None,
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.NOT_APPLICABLE
        ),
        due_at=DUE if verified else None,
        due_date_basis=_basis(verified=verified),
        source_authority="dotmac-billing",
        evidence_ref="import",
        mapping_version="1",
    )
    return ReceivableExposureV2(
        scope=SCOPE,
        source_owner="dotmac-billing",
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        exposure_ref=f"document:{DOCUMENT}",
        document_id=DOCUMENT,
        billing_account_id=ACCOUNT,
        basis_id=uuid4(),
        source_version=2,
        posting_group_watermark=uuid4(),
        collection=evidence,
        financial_state="open",
        collectible_receivable=Money.of("50.00", NGN),
        state_fingerprint="e" * 64,
        observed_at=NOW,
    )


class _Billing:
    """Records the terms reads; ``grace`` answers per instant like dated terms."""

    def __init__(self, monkeypatch, exposure, *, contract="postpaid", grace=None):
        self.terms_at: list[datetime] = []
        self.contract = contract
        self.grace = grace or (lambda at: timedelta(days=60))
        self.exposure = exposure

        def exposure_read(db, *, scope, document_id):
            if isinstance(self.exposure, BillingRuleViolation):
                raise self.exposure
            return self.exposure

        def terms(db, *, scope, account_id, at):
            assert account_id == ACCOUNT
            self.terms_at.append(at)
            return SimpleNamespace(
                contract_type=SimpleNamespace(value=self.contract),
                postpaid_grace=SimpleNamespace(value=self.grace(at)),
            )

        monkeypatch.setattr(x1, "get_document_exposure", exposure_read)
        monkeypatch.setattr(x1, "get_effective_account_terms", terms)

    def read(
        self, *, scope=SCOPE, owner="dotmac-billing", ref=None, reason=None, floor=None
    ):
        extra = {} if reason is None else {"reason_code": reason}
        reader = BillingReceivablesReader(
            object(),  # type: ignore[arg-type]
            terms_floor_at=floor,
            **extra,
        )
        return reader.read(
            scope=scope,
            source_owner=owner,
            exposure_ref=ref or f"document:{DOCUMENT}",
            as_of=NOW,
        )


def test_verified_postpaid_v1_maps_and_reads_the_grace_at_the_due_date(
    monkeypatch,
) -> None:
    billing = _Billing(monkeypatch, _v1())
    result = billing.read()
    assert isinstance(result, PositionReadOk)
    observation = result.position
    assert observation.subject_ref == f"billing-account:{ACCOUNT}"
    assert observation.subject_ref == subject_ref_for(ACCOUNT)
    assert observation.service_ref == "service:one"
    assert observation.due_at == DUE
    assert observation.due_date_status == "verified"
    assert observation.access_hold_from == DUE + timedelta(days=60)
    assert observation.reason_code == "invoice_overdue"
    assert billing.terms_at == [ISSUED, DUE]  # contract at issue, grace at due


def test_a_terms_change_after_the_due_date_cannot_move_the_hold_instant(
    monkeypatch,
) -> None:
    # Dated terms: the account's 90 days takes effect the day after the due date.
    def grace(at: datetime) -> timedelta:
        return timedelta(days=90) if at > DUE else timedelta(days=60)

    result = _Billing(monkeypatch, _v1(), grace=grace).read()
    assert isinstance(result, PositionReadOk)
    assert result.position.access_hold_from == DUE + timedelta(days=60)


def test_the_account_override_is_whatever_billing_resolves(monkeypatch) -> None:
    result = _Billing(monkeypatch, _v1(), grace=lambda at: timedelta(days=999)).read()
    assert isinstance(result, PositionReadOk)
    assert result.position.access_hold_from == DUE + timedelta(days=999)


def test_unknown_due_date_carries_no_due_and_no_hold_and_reads_no_terms(
    monkeypatch,
) -> None:
    billing = _Billing(monkeypatch, _v1(verified=False))
    result = billing.read()
    assert isinstance(result, PositionReadOk)
    assert result.position.due_at is None
    assert result.position.due_date_status == "unknown_unverified"
    assert result.position.access_hold_from is None
    assert billing.terms_at == []


@pytest.mark.parametrize("state", ["resolved", "cancelled"])
def test_a_closed_exposure_reads_no_terms_and_has_no_hold(monkeypatch, state) -> None:
    amount = "0.00"
    billing = _Billing(monkeypatch, _v1(state=state, amount=amount))
    result = billing.read()
    assert isinstance(result, PositionReadOk)
    assert result.position.financial_state == state
    assert result.position.access_hold_from is None
    assert billing.terms_at == []


def test_prepaid_is_refused_before_the_grace_is_read(monkeypatch) -> None:
    billing = _Billing(monkeypatch, _v1(), contract="prepaid")
    assert billing.read() == PositionUnavailable("not_postpaid", None)
    assert billing.terms_at == [ISSUED]


def test_verified_basis_without_an_issue_instant_is_unavailable(monkeypatch) -> None:
    # Billing forbids this shape; the reader still fails closed if it ever appears.
    native = _v1()
    fields = (
        "scope source_owner exposure_ref billing_account_id service_ref "
        "collection_timing source_version source_authority projection_mode "
        "completeness completeness_reason_code state_fingerprint observed_at "
        "service_period due_at financial_state collectible_receivable"
    ).split()
    patched = SimpleNamespace(**{name: getattr(native, name) for name in fields})
    patched.due_date_basis = SimpleNamespace(
        status=DueDateBasisStatus.VERIFIED, issued_at=None
    )
    monkeypatch.setattr(x1, "ReceivableExposureV2", ())  # route to the native path
    billing = _Billing(monkeypatch, patched)
    assert billing.read() == PositionUnavailable("issue_instant_unknown", None)


def test_imported_v2_maps_through_the_collection_evidence(monkeypatch) -> None:
    result = _Billing(monkeypatch, _v2()).read()
    assert isinstance(result, PositionReadOk)
    observation = result.position
    assert observation.subject_ref == f"billing-account:{ACCOUNT}"
    assert observation.service_ref == "service:two"
    assert observation.due_at == DUE
    assert observation.access_hold_from == DUE + timedelta(days=60)
    assert observation.source_version == 2


def test_imported_v2_with_an_unverified_collection_is_never_chased(monkeypatch) -> None:
    result = _Billing(monkeypatch, _v2(verified=False)).read()
    assert isinstance(result, PositionReadOk)
    assert result.position.due_date_status == "unknown_unverified"
    assert result.position.due_at is None
    assert result.position.access_hold_from is None


def test_unknown_document_authority_and_malformed_references(monkeypatch) -> None:
    missing = BillingRuleViolation("not_found", "no such invoice")
    billing = _Billing(monkeypatch, missing)
    assert billing.read() == PositionUnknown("dotmac-billing", f"document:{DOCUMENT}")
    assert billing.read(owner="dotmac-erp") == PositionAuthorityMismatch(
        "dotmac-billing", "dotmac-erp"
    )
    for ref in ("invoice:1", "document:not-a-uuid", "nonsense", "document:"):
        assert isinstance(billing.read(ref=ref), PositionUnknown), ref


def test_other_billing_refusals_raise_but_missing_terms_is_unavailable(
    monkeypatch,
) -> None:
    stale = BillingRuleViolation("exposure_fact_stale", "stale")
    with pytest.raises(BillingRuleViolation):
        _Billing(monkeypatch, stale).read()
    billing = _Billing(monkeypatch, _v1())

    def other(db, *, scope, account_id, at):
        raise BillingRuleViolation("invalid_account_id", "bad")

    monkeypatch.setattr(x1, "get_effective_account_terms", other)
    with pytest.raises(BillingRuleViolation):
        billing.read()

    def no_terms(db, *, scope, account_id, at):
        raise BillingRuleViolation("terms_default_missing", "no default")

    monkeypatch.setattr(x1, "get_effective_account_terms", no_terms)
    assert billing.read() == PositionUnavailable("account_terms_missing", None)


def test_terms_are_read_at_the_floor_for_an_item_issued_before_the_flip(
    monkeypatch,
) -> None:
    flip = NOW + timedelta(days=5)
    billing = _Billing(monkeypatch, _v1())
    seen = billing.terms_at

    def terms(db, *, scope, account_id, at):
        seen.append(at)
        if at < flip:  # nothing can be recorded before the flip
            raise BillingRuleViolation("terms_default_missing", "no default")
        return SimpleNamespace(
            contract_type=SimpleNamespace(value="postpaid"),
            postpaid_grace=SimpleNamespace(value=timedelta(days=60)),
        )

    monkeypatch.setattr(x1, "get_effective_account_terms", terms)
    assert billing.read() == PositionUnavailable("account_terms_missing", None)
    seen.clear()
    result = billing.read(floor=flip)
    assert isinstance(result, PositionReadOk)
    assert seen == [flip, DUE]  # issued before the flip reads at the floor
    assert result.position.access_hold_from == DUE + timedelta(days=60)


def test_a_floor_earlier_than_the_instants_changes_nothing(monkeypatch) -> None:
    billing = _Billing(monkeypatch, _v1())
    assert isinstance(billing.read(floor=ISSUED - timedelta(days=9)), PositionReadOk)
    assert billing.terms_at == [ISSUED, DUE]


def test_the_reader_is_tenant_plane_only_and_requires_a_reason(monkeypatch) -> None:
    billing = _Billing(monkeypatch, _v1())
    with pytest.raises(TypeError):
        billing.read(scope=PlatformScope())
    with pytest.raises(ValueError, match="reason_code"):
        billing.read(reason=" ")


def test_a_naive_terms_floor_is_refused_before_any_billing_read(monkeypatch) -> None:
    billing = _Billing(monkeypatch, _v1())
    with pytest.raises(ValueError, match="terms_floor_at must be timezone-aware"):
        billing.read(floor=datetime(2026, 11, 1))
    assert billing.terms_at == []


def test_a_opening_late_debit_v1_exposure_reads_no_hold_without_automated_evidence(
    monkeypatch,
) -> None:
    """The V1 branch turns a due date into a hold, so Billing must not supply one
    for an opening late debit whose collection evidence is unverified."""
    from dataclasses import replace

    from dotmac_billing.opening_late_items import exposure_due

    evidence = OpeningCollectionEvidenceV1(
        "unknown_unverified",
        "customer:1",
        None,
        "arrears",
        ServicePeriodEvidenceV1(status=ServicePeriodStatus.NOT_APPLICABLE),
        DUE,
        _basis(),
        "dotmac-billing",
        "late-item",
        "1",
    )
    due_at, basis = exposure_due(evidence, DUE, _basis())
    exposure = replace(_v1(), due_at=due_at, due_date_basis=basis)
    billing = _Billing(monkeypatch, exposure)
    result = billing.read()
    assert isinstance(result, PositionReadOk)
    assert result.position.due_at is None
    assert result.position.due_date_status == "unknown_unverified"
    assert result.position.access_hold_from is None
    assert billing.terms_at == []
