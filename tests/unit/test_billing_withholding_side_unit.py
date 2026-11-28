"""Expected-withholding evidence must carry the Tax ``withholding`` side (a16).

Only new writes are refused. Stored evidence keeps decoding, and the shared
validators are deliberately not tightened.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_billing import AcceptRatedObligationV2, service
from dotmac_billing.contracts import (
    WithholdingDischargeEffectV1,
    WithholdingDischargeRecordV1,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.queries import get_withholding_discharge_record
from dotmac_billing.service import _jsonable
from dotmac_billing.tax_evidence import validate_invoice_tax_evidence
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_dependency_evidence as dependency
from tests import test_billing_tax_determination_evidence as fixtures
from tests.unit.test_billing_withholding_contracts import evidence as v1_evidence

CODE = "billing.withholding_transaction_side_invalid"


def _money(value):
    return Money(Decimal(value), Currency("NGN", 2))


def _wht(scope, side=None):
    return fixtures._evidence(
        scope, purpose="expected_withholding", taxes=("5.00",), side=side
    )


def _rated(scope, evidence):
    old = authoring._command(uuid4(), scope)
    obligation = fixtures._command(uuid4(), scope, (evidence,)).obligation
    assert isinstance(obligation, AcceptRatedObligationV2)
    assert {f.name for f in fields(old.obligation)} <= {
        f.name for f in fields(obligation)
    }
    return obligation


def test_fixture_defaults_to_the_withholding_side_for_withholding_purpose():
    scope = TenantScope(uuid4())
    assert _wht(scope).determination.transaction_side == "withholding"
    assert fixtures._evidence(scope).determination.transaction_side == "output"


def test_helper_refuses_a_non_withholding_side_only_for_withholding_purpose():
    scope = TenantScope(uuid4())
    service._require_withholding_side((_wht(scope),))
    service._require_withholding_side((fixtures._evidence(scope),))
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_withholding_side((_wht(scope, "output"),))
    assert refused.value.code == CODE


@pytest.mark.parametrize("side", ["output", "input", "liability", "sale"])
def test_intake_refuses_expected_withholding_on_a_vat_side(side):
    scope = TenantScope(uuid4())
    command = _rated(scope, _wht(scope, side))
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_rated_tax_evidence(command, scope=scope)
    assert refused.value.code == CODE


def test_intake_accepts_the_withholding_side():
    scope = TenantScope(uuid4())
    service._require_rated_tax_evidence(_rated(scope, _wht(scope)), scope=scope)


def test_shared_validator_is_not_tightened_so_stored_rows_still_decode():
    scope = TenantScope(uuid4())
    stored = _wht(scope, "output")
    validate_invoice_tax_evidence(
        (stored,), scope=scope, tax_amount=Money(Decimal(0), Currency("NGN", 2))
    )


@pytest.mark.parametrize("side", ["sale", "output", "input", "liability"])
def test_discharge_refuses_a_non_withholding_side(side):
    scope = TenantScope(uuid4())
    command = billing._withholding_command(uuid4(), uuid4(), scope=scope)
    assert command.tax_evidence.transaction_side == "withholding"
    bad = replace(
        command, tax_evidence=replace(command.tax_evidence, transaction_side=side)
    )
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_withholding_command(scope=scope, command=bad)
    assert refused.value.code == CODE
    service._require_withholding_command(scope=scope, command=command)


def test_stored_withholding_evidence_with_another_side_still_constructs():
    assert replace(v1_evidence(), transaction_side="sale").transaction_side == "sale"


def test_issue_time_freeze_fails_closed_on_stored_wrong_side(monkeypatch):
    scope = TenantScope(uuid4())
    stored = _wht(scope, "output")
    added: list[object] = []

    class Rows:
        def scalars(self):
            return iter([SimpleNamespace(obligation_id=uuid4(), id=uuid4())])

    class Db:
        def execute(self, statement):
            return Rows()

        def add(self, row):
            added.append(row)

        def flush(self):
            raise AssertionError("nothing may be written")

    obligation = SimpleNamespace(
        id=uuid4(),
        tax_evidence=[{}],
        tax_amount=Decimal("0"),
        currency="NGN",
        minor_units=2,
    )
    monkeypatch.setattr(service, "_one", lambda *a, **k: obligation)
    monkeypatch.setattr(service, "_decode_tax_evidence", lambda *a, **k: (stored,))
    document = SimpleNamespace(document_kind="invoice", id=uuid4())
    with pytest.raises(BillingRuleViolation) as refused:
        service._freeze_withholding_components(
            Db(), scope=scope, plane=service._models(scope), document=document
        )
    assert refused.value.code == CODE
    assert added == []


# --- get_withholding_discharge_record -------------------------------------

RECORDED = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
OCCURRED = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Session:
    no_autoflush = nullcontext()

    def __init__(self, *rows):
        self.rows = list(rows)

    def execute(self, statement):
        return _Result(self.rows.pop(0))


def _stored(scope=None, *, reversal=False):
    tax = v1_evidence()
    scope = tax.scope if scope is None else scope
    tax = replace(tax, scope=scope)
    amount = _money("-5000" if reversal else "5000")
    original = uuid4() if reversal else None
    effect = WithholdingDischargeEffectV1(
        uuid4(),
        tax.document_id,
        "claim:1",
        _money("5000"),
        amount,
        tax,
        "finance:1",
        "audit:1",
        reverses_discharge_id=original,
    )
    group = uuid4()
    row = {
        "id": effect.discharge_id,
        "document_id": effect.document_id,
        "billing_account_id": tax.billing_account_id,
        "posting_group_id": group,
        "amount_delta": amount.amount,
        "currency": "NGN",
        "minor_units": 2,
        "claimed_amount": Decimal("5000"),
        "claim_ref": "claim:1",
        "settlement_id": None,
        "allocation_id": None,
        "reverses_discharge_id": original,
        "tax_source_owner": tax.source_owner,
        "determination_ref": tax.determination_ref,
        "component_ref": tax.component_ref,
        "evidence": _jsonable(effect),
        "created_at": RECORDED,
    }
    kind = "withholding_reversed" if reversal else "withholding_accepted"
    grp = {
        "id": group,
        "billing_account_id": tax.billing_account_id,
        "group_kind": kind,
        "occurred_at": OCCURRED,
    }
    return effect, row, grp, tax


@pytest.mark.parametrize("scope_kind", ["platform", "tenant"])
@pytest.mark.parametrize("reversal", [False, True])
def test_record_carries_recording_and_business_time(scope_kind, reversal):
    scope = PlatformScope() if scope_kind == "platform" else TenantScope(uuid4())
    effect, row, grp, tax = _stored(scope, reversal=reversal)
    record = get_withholding_discharge_record(
        _Session(row, row, grp), scope=scope, discharge_id=effect.discharge_id
    )
    assert isinstance(record, WithholdingDischargeRecordV1)
    assert record.recorded_at == RECORDED
    assert record.occurred_at == OCCURRED
    assert record.billing_account_id == tax.billing_account_id
    assert record.document_id == effect.document_id
    assert record.posting_group_id == row["posting_group_id"]
    assert record.reverses_discharge_id == row["reverses_discharge_id"]
    assert record.effect == effect


def test_record_not_found():
    with pytest.raises(BillingRuleViolation) as refused:
        get_withholding_discharge_record(
            _Session(None), scope=PlatformScope(), discharge_id=uuid4()
        )
    assert refused.value.code == "billing.not_found"


@pytest.mark.parametrize(
    "change",
    [
        {"billing_account_id": uuid4()},
        {"group_kind": "withholding_reversed"},
        {"group_kind": "settlement"},
    ],
)
def test_record_refuses_a_foreign_or_wrong_kind_posting_group(change):
    scope = PlatformScope()
    effect, row, grp, _ = _stored(scope)
    with pytest.raises(BillingRuleViolation) as refused:
        get_withholding_discharge_record(
            _Session(row, row, {**grp, **change}),
            scope=scope,
            discharge_id=effect.discharge_id,
        )
    assert refused.value.code == "billing.withholding_evidence_corrupt"


def test_record_refuses_a_missing_posting_group():
    scope = PlatformScope()
    effect, row, _, _ = _stored(scope)
    with pytest.raises(BillingRuleViolation) as refused:
        get_withholding_discharge_record(
            _Session(row, row, None), scope=scope, discharge_id=effect.discharge_id
        )
    assert refused.value.code == "billing.withholding_evidence_corrupt"


def test_record_requires_an_explicit_scope():
    with pytest.raises(TypeError):
        get_withholding_discharge_record(
            _Session(),
            scope=object(),
            discharge_id=uuid4(),  # type: ignore[arg-type]
        )


# --- V3 intake and V2 discharge evidence -----------------------------------


def _v2_wrong_side(scope, side):
    pair = dependency._evidence(scope)
    wht = replace(
        pair[1], determination=replace(pair[1].determination, transaction_side=side)
    )
    return (pair[0], wht)


@pytest.mark.parametrize("side", ["output", "input", "liability"])
def test_v3_intake_refuses_expected_withholding_on_another_side(side):
    scope = TenantScope(uuid4())
    tax = _v2_wrong_side(scope, side)
    obligation = dependency._command(uuid4(), scope, tax).obligation
    assert type(obligation).__name__ == "AcceptRatedObligationV3"
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_rated_tax_evidence(obligation, scope=scope)
    assert refused.value.code == CODE


def test_v3_intake_accepts_the_withholding_side():
    scope = TenantScope(uuid4())
    obligation = dependency._command(
        uuid4(), scope, dependency._evidence(scope)
    ).obligation
    service._require_rated_tax_evidence(obligation, scope=scope)


def test_v2_discharge_evidence_refuses_a_non_withholding_side():
    scope = TenantScope(uuid4())
    account, invoice = uuid4(), uuid4()
    base = billing._withholding_command(account, invoice, scope=scope)
    good = dependency._discharge_evidence(
        scope, account, invoice, dependency._evidence(scope)[1]
    )
    assert good.transaction_side == "withholding"
    service._require_withholding_command(
        scope=scope, command=replace(base, tax_evidence=good)
    )
    bad = dependency._discharge_evidence(
        scope, account, invoice, _v2_wrong_side(scope, "output")[1]
    )
    assert bad.transaction_side == "output"
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_withholding_command(
            scope=scope, command=replace(base, tax_evidence=bad)
        )
    assert refused.value.code == CODE


def test_issue_precheck_refuses_stored_wrong_side_before_any_write(monkeypatch):
    scope = TenantScope(uuid4())

    class Rows:
        def scalars(self):
            return iter([SimpleNamespace(obligation_id=uuid4(), id=uuid4())])

    class Db:
        def execute(self, statement):
            return Rows()

    obligation = SimpleNamespace(
        id=uuid4(),
        tax_evidence=[{}],
        tax_amount=Decimal("0"),
        currency="NGN",
        minor_units=2,
    )
    monkeypatch.setattr(service, "_one", lambda *a, **k: obligation)
    document = SimpleNamespace(document_kind="invoice", id=uuid4())
    args = {"scope": scope, "plane": service._models(scope), "document": document}
    monkeypatch.setattr(
        service, "_decode_tax_evidence", lambda *a, **k: (_wht(scope, "output"),)
    )
    with pytest.raises(BillingRuleViolation) as refused:
        service._require_stored_withholding_sides(Db(), **args)
    assert refused.value.code == CODE
    monkeypatch.setattr(service, "_decode_tax_evidence", lambda *a, **k: (_wht(scope),))
    service._require_stored_withholding_sides(Db(), **args)
