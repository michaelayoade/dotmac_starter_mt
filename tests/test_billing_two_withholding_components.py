"""Two deduction-at-source components (WHT and stamp duty) freeze and cap apart.

Live PostgreSQL proofs. A dotmac-tax determination may elect more than one
withholding component (a WHT code and a contract stamp-duty code). Billing
freezes each elected component as its own structural row and every discharge
names exactly one of them, capped by that component alone. Tests only.
"""

# ruff: noqa: S608
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import service
from dotmac_billing.commands import ApplyAvailableCreditCommand
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import (
    accept_withholding_discharge,
    apply_available_credit,
    create_billing_account,
    rebuild_receivable_position,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_determination_evidence as evidence_fixtures

_money = billing._money
WHT, STAMP = 0, 1
AMOUNTS = {WHT: "5.00", STAMP: "0.50"}


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _scope(admin_url):
    return TenantScope(billing._create_tenants(admin_url, 1)[0])


def _two_component_evidence(scope):
    item = evidence_fixtures._evidence(
        scope, purpose="expected_withholding", taxes=(AMOUNTS[WHT], AMOUNTS[STAMP])
    )
    components = tuple(
        replace(
            component,
            party_category=category,
            party_classification_id=uuid4(),
        )
        for component, category in zip(
            item.determination.components, ("wht-elected", "govt-agency"), strict=True
        )
    )
    return replace(
        item, determination=replace(item.determination, components=components)
    )


def _invoice(session, scope):
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=2,
    )
    evidence = _two_component_evidence(scope)
    issued = authoring._issue(
        session, scope, evidence_fixtures._command(account.id, scope, (evidence,))
    )
    session.flush()
    return account, issued.invoice_id, evidence


def _discharge(
    account, invoice_id, scope, evidence, which, *, accepted=None, **changes
):
    """A discharge of one named component, mirroring the retained Tax result."""
    result = evidence.determination
    component = result.components[which]
    accepted = accepted or AMOUNTS[which]
    base = billing._withholding_command(
        account.id, invoice_id, scope=scope, suffix=str(uuid4()), claimed=accepted
    )
    fields = {
        "determination_ref": str(result.determination_set_id),
        "component_ref": str(component.determination_id),
        "result_fingerprint": result.result_fingerprint,
        "source_fingerprint": result.source_fingerprint,
        "rule_ref": str(component.rule_id),
        "rule_version": str(component.rule_version),
        "amount": component.tax_amount,
        "taxable_basis": component.base_amount,
    }
    fields.update(changes)
    fields["lines"] = (
        replace(
            base.tax_evidence.lines[0],
            taxable_amount=fields["taxable_basis"],
            tax_amount=fields["amount"],
        ),
    )
    return replace(
        base,
        tax_evidence=replace(base.tax_evidence, **fields),
        claimed_amount=_money(accepted),
        accepted_amount=_money(accepted),
    )


def _frozen(session, invoice_id):
    return session.execute(
        text(
            "SELECT tax_code_id, component_ref, tax_amount, party_category "
            "FROM mod_billing.document_withholding_components "
            "WHERE document_id = :d ORDER BY tax_amount DESC"
        ),
        {"d": invoice_id},
    ).all()


def test_issue_freezes_each_elected_component_as_its_own_row(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, invoice_id, evidence = _invoice(session, scope)
        rows = _frozen(session, invoice_id)
        components = evidence.determination.components
        assert [row.tax_amount for row in rows] == [Decimal("5.00"), Decimal("0.50")]
        assert [row.tax_code_id for row in rows] == [
            components[WHT].tax_code_id,
            components[STAMP].tax_code_id,
        ]
        assert rows[0].tax_code_id != rows[1].tax_code_id
        assert {row.component_ref for row in rows} == {
            str(c.determination_id) for c in components
        }
        assert [row.party_category for row in rows] == ["wht-elected", "govt-agency"]
        session.rollback()


def test_each_component_is_discharged_separately(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope)
        stamp = accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice_id, scope, evidence, STAMP),
        )
        wht = accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice_id, scope, evidence, WHT),
        )
        assert stamp.id != wht.id
        assert (stamp.amount_delta, wht.amount_delta) == (
            Decimal("0.50"),
            Decimal("5.00"),
        )
        assert stamp.component_ref != wht.component_ref
        # Each component is taken once; a second draw on either is a conflict.
        for which in (WHT, STAMP):
            with pytest.raises(BillingConflict):
                accept_withholding_discharge(
                    session,
                    scope=scope,
                    command=_discharge(account, invoice_id, scope, evidence, which),
                )
        session.rollback()


def test_over_discharging_one_component_is_refused_and_leaves_the_other_intact(
    scratch,
):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope)
        # Stamp duty evidence larger than its own frozen 0.50 is refused, even
        # though the invoice's combined frozen withholding (5.50) would cover it.
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account,
                    invoice_id,
                    scope,
                    evidence,
                    STAMP,
                    accepted="0.60",
                    amount=_money("0.60"),
                ),
            )
        assert refused.value.code == "billing.withholding_evidence_differs_from_frozen"
        # The WHT component cannot absorb the stamp-duty amount either.
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account,
                    invoice_id,
                    scope,
                    evidence,
                    WHT,
                    accepted="5.50",
                    amount=_money("5.50"),
                ),
            )
        assert refused.value.code == "billing.withholding_evidence_differs_from_frozen"
        # Nothing was drawn by the refusals: both components still discharge.
        for which in (WHT, STAMP):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(account, invoice_id, scope, evidence, which),
            )
        session.rollback()


def test_database_caps_each_component_when_the_service_is_bypassed(
    scratch, monkeypatch
):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    monkeypatch.setattr(service, "_require_frozen_withholding", lambda *a, **k: None)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope)
        with pytest.raises(DBAPIError, match="leaves its frozen component"):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account,
                    invoice_id,
                    scope,
                    evidence,
                    STAMP,
                    accepted="0.60",
                    amount=_money("0.60"),
                ),
            )
        session.rollback()


def test_credit_room_reserves_both_frozen_components(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope)
        billing_settlement = billing._settlement_command(
            account.id, scope=scope, source_key=f"credit-{uuid4()}", amount="100.00"
        )
        service.accept_settlement(
            session,
            scope=scope,
            command=billing_settlement,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )

        def draw(amount, tag):
            return apply_available_credit(
                session,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account.id, invoice_id, _money(amount), billing.NOW, tag
                ),
            )

        # Total 100 less frozen 5.00 + 0.50: credit may draw at most 94.50.
        with pytest.raises(BillingRuleViolation) as refused:
            draw("94.51", f"credit-over-{invoice_id}")
        assert refused.value.code == "billing.credit_exceeds_non_withholding_room"
        draw("94.50", f"credit-exact-{invoice_id}")
        for which in (WHT, STAMP):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(account, invoice_id, scope, evidence, which),
            )
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert position.collectible_receivable == Decimal("0")
        session.rollback()
