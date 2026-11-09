"""B2: expected withholding is frozen at issue and bounds every later draw.

Live PostgreSQL proofs for ``bi_0016``. The WHT election is a dotmac-tax party
classification; Billing only freezes the elected determination component and
verifies later evidence against it.
"""

# SQL identifiers below are fixed Billing-owned table names.
# ruff: noqa: S608
from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import service
from dotmac_billing.commands import (
    ApplyAvailableCreditCommand,
    ReverseWithholdingDischarge,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import (
    accept_settlement,
    accept_withholding_discharge,
    apply_available_credit,
    create_billing_account,
    rebuild_receivable_position,
    reverse_withholding_discharge,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_determination_evidence as evidence_fixtures

_money = billing._money


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _wht_evidence(scope, *, elected: bool, amount: str = "5.00"):
    item = evidence_fixtures._evidence(
        scope, purpose="expected_withholding", taxes=(amount,)
    )
    if not elected:
        return item
    component = replace(
        item.determination.components[0],
        party_category="wht-elected",
        party_classification_id=uuid4(),
    )
    return replace(
        item, determination=replace(item.determination, components=(component,))
    )


def _invoice(session, scope, *, elected: bool, amount: str = "5.00"):
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=2,
    )
    evidence = _wht_evidence(scope, elected=elected, amount=amount)
    issued = authoring._issue(
        session, scope, evidence_fixtures._command(account.id, scope, (evidence,))
    )
    session.flush()
    return account, issued.invoice_id, evidence


def _discharge(
    account, invoice_id, scope, evidence, *, accepted="5.00", suffix=None, **changes
):
    """A discharge command whose Tax evidence mirrors the retained result."""
    suffix = suffix or str(uuid4())
    result = evidence.determination
    component = result.components[0]
    base = billing._withholding_command(
        account.id, invoice_id, scope=scope, suffix=suffix, claimed=accepted
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
        "lines": (
            replace(
                base.tax_evidence.lines[0],
                taxable_amount=component.base_amount,
                tax_amount=component.tax_amount,
            ),
        ),
    }
    fields.update(changes)
    if "lines" not in changes:
        fields["lines"] = (
            replace(
                base.tax_evidence.lines[0],
                taxable_amount=fields["taxable_basis"],
                tax_amount=fields["amount"],
            ),
        )
    tax = replace(base.tax_evidence, **fields)
    return replace(
        base,
        tax_evidence=tax,
        claimed_amount=_money(accepted),
        accepted_amount=_money(accepted),
    )


def _components(session, invoice_id):
    return session.execute(
        text(
            "SELECT tax_amount, party_classification_id, tax_source_owner "
            "FROM mod_billing.document_withholding_components WHERE document_id = :d"
        ),
        {"d": invoice_id},
    ).all()


def _scope(admin_url):
    return TenantScope(billing._create_tenants(admin_url, 1)[0])


def test_elected_component_is_frozen_structurally_at_issue(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, invoice_id, evidence = _invoice(session, scope, elected=True)
        rows = _components(session, invoice_id)
        component = evidence.determination.components[0]
        assert len(rows) == 1
        assert rows[0].tax_amount == Decimal("5.00")
        assert rows[0].party_classification_id == component.party_classification_id
        assert rows[0].tax_source_owner == "dotmac-tax"
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE mod_billing.document_withholding_components "
                    "SET tax_amount = 9 WHERE document_id = :d"
                ),
                {"d": invoice_id},
            )
        session.rollback()


def test_unelected_invoice_freezes_nothing_and_discharge_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=False)
        assert _components(session, invoice_id) == []
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(account, invoice_id, scope, evidence),
            )
        assert refused.value.code == "billing.withholding_not_elected"
        session.rollback()


def test_database_refuses_a_discharge_without_a_frozen_component(scratch, monkeypatch):
    """The service refusal is not the only gate: the guard and FK hold alone."""
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    monkeypatch.setattr(service, "_require_frozen_withholding", lambda *a, **k: None)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=False)
        with pytest.raises(DBAPIError, match="frozen component"):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(account, invoice_id, scope, evidence),
            )
        session.rollback()


def test_over_cap_discharge_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=True)
        # Evidence larger than what was frozen is not the frozen result.
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account,
                    invoice_id,
                    scope,
                    evidence,
                    accepted="6.00",
                    amount=_money("6.00"),
                ),
            )
        assert refused.value.code == "billing.withholding_evidence_differs_from_frozen"
        first = accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice_id, scope, evidence, accepted="3.00"),
        )
        # The component is accepted once; the remainder cannot be taken as a
        # second draw (existing permanent component claim), and a reversal puts
        # the full frozen amount back within reach of exactly one new draw.
        with pytest.raises(BillingConflict):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account, invoice_id, scope, evidence, accepted="2.00"
                ),
            )
        reverse_withholding_discharge(
            session,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                first.id,
                f"reverse:{first.id}",
                f"reverse-source:{first.id}",
                "finance-user:fixture",
                "trusted-assembly-audit:fixture",
                billing.NOW,
            ),
        )
        session.rollback()


def test_database_enforces_the_cap_when_the_service_is_bypassed(scratch, monkeypatch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    monkeypatch.setattr(service, "_require_frozen_withholding", lambda *a, **k: None)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=True)
        with pytest.raises(DBAPIError, match="leaves its frozen component"):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account,
                    invoice_id,
                    scope,
                    evidence,
                    accepted="6.00",
                    amount=_money("6.00"),
                ),
            )
        session.rollback()


def test_evidence_that_differs_from_the_frozen_result_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=True)
        for changes, code in (
            ({"source_owner": "customer-tax"}, "billing.withholding_tax_owner"),
            (
                {"result_fingerprint": "rv1:" + "c" * 64},
                "billing.withholding_evidence_differs_from_frozen",
            ),
            ({"component_ref": str(uuid4())}, "billing.withholding_not_elected"),
        ):
            with pytest.raises(BillingRuleViolation) as refused:
                accept_withholding_discharge(
                    session,
                    scope=scope,
                    command=_discharge(account, invoice_id, scope, evidence, **changes),
                )
            assert refused.value.code == code
        session.rollback()


def _fund_credit(session, scope, account, amount):
    accept_settlement(
        session,
        scope=scope,
        command=billing._settlement_command(
            account.id, scope=scope, source_key=f"credit-{uuid4()}", amount=amount
        ),
        accepted_confirmation_evidence=frozenset({"provider_settled"}),
    )


def test_credit_is_drawn_before_discharge_and_the_cap_holds(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=True)
        _fund_credit(session, scope, account, "100.00")

        def draw(amount, tag):
            return apply_available_credit(
                session,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account.id, invoice_id, _money(amount), billing.NOW, tag
                ),
            )

        # Total 100, frozen and undischarged WHT 5: credit may draw at most 95.
        with pytest.raises(BillingRuleViolation) as refused:
            draw("100.00", f"credit-full-{invoice_id}")
        assert refused.value.code == "billing.credit_exceeds_non_withholding_room"
        draw("95.00", f"credit-95-{invoice_id}")
        # The 5.00 left is reserved for the discharge; no credit may take it.
        with pytest.raises(BillingRuleViolation) as refused:
            draw("0.01", f"credit-more-{invoice_id}")
        assert refused.value.code == "billing.credit_exceeds_non_withholding_room"
        accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice_id, scope, evidence, accepted="5.00"),
        )
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert position.collectible_receivable == Decimal("0")
        assert position.available_credit == Decimal("5")
        session.rollback()


def test_a_discharged_part_releases_no_credit_room_beyond_the_remainder(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = _invoice(session, scope, elected=True)
        _fund_credit(session, scope, account, "100.00")
        accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice_id, scope, evidence, accepted="2.00"),
        )
        # Outstanding 98, undischarged frozen 3: room 95.
        with pytest.raises(BillingRuleViolation) as refused:
            apply_available_credit(
                session,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account.id, invoice_id, _money("95.01"), billing.NOW, "over"
                ),
            )
        assert refused.value.code == "billing.credit_exceeds_non_withholding_room"
        apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account.id, invoice_id, _money("95.00"), billing.NOW, "exact"
            ),
        )
        session.rollback()


def test_foreign_key_validated_on_a_clean_database_and_history_stays_not_valid(
    scratch, monkeypatch
):
    """A discharge accepted before the freeze stays, NOT VALID and noted."""
    from alembic import command as alembic_command
    from alembic.config import Config

    admin_url, _, _ = scratch
    engine = billing.create_engine(admin_url)
    with engine.connect() as connection:
        valid = connection.execute(
            text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conname = 'fk_withholding_discharges_frozen_component'"
            )
        ).scalar_one()
    engine.dispose()
    assert valid is True

    with billing._scratch_database(
        (ModulePlane.TENANT,), target_revision="bi_0015_line_price_version_fk"
    ) as (history_admin, history_tenant, _):
        scope = _scope(history_admin)
        monkeypatch.setattr(
            service, "_require_frozen_withholding", lambda *a, **k: None
        )
        with billing._session(history_tenant, scope) as session:
            account, invoice_id, evidence = _invoice(session, scope, elected=False)
            historical = accept_withholding_discharge(
                session,
                scope=scope,
                command=_discharge(
                    account, invoice_id, scope, evidence, accepted="4.00"
                ),
            )
            historical_id = historical.id
            session.commit()
        config = Config(str(billing.REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(billing.REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{billing.KERNEL_VERSIONS} {billing.ASSEMBLY_VERSIONS} "
            f"{billing.BILLING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=(ModulePlane.TENANT,)),
        )
        old = os.environ.get("MIGRATION_DATABASE_URL")
        os.environ["MIGRATION_DATABASE_URL"] = history_admin
        try:
            alembic_command.upgrade(config, "bi_0016_withholding_components")
        finally:
            if old is None:
                os.environ.pop("MIGRATION_DATABASE_URL", None)
            else:
                os.environ["MIGRATION_DATABASE_URL"] = old
        engine = billing.create_engine(history_admin)
        with engine.connect() as connection:
            validated, note = connection.execute(
                text(
                    "SELECT convalidated, obj_description(oid, 'pg_constraint') "
                    "FROM pg_constraint "
                    "WHERE conname = 'fk_withholding_discharges_frozen_component'"
                )
            ).one()
        engine.dispose()
        assert validated is False
        assert "exceptions review" in note and "backfilled" in note
        monkeypatch.undo()
        with billing._session(history_tenant, scope) as session:
            # Unrepaired history survives, and the new rules apply to new rows.
            assert session.execute(
                text(
                    "SELECT amount_delta FROM mod_billing.withholding_discharges "
                    "WHERE id = :id"
                ),
                {"id": historical_id},
            ).scalar_one() == Decimal("4")
            account2, invoice2, evidence2 = _invoice(session, scope, elected=False)
            with pytest.raises(BillingRuleViolation) as refused:
                accept_withholding_discharge(
                    session,
                    scope=scope,
                    command=_discharge(account2, invoice2, scope, evidence2),
                )
            assert refused.value.code == "billing.withholding_not_elected"
            session.rollback()


# --- governed late freeze (invoices issued before bi_0016 / imported) ---------


def _as_of_issue(scope, *, amount: str = "5.00", on=None):
    """A dotmac-tax expected-withholding determination as of the issue date."""
    from datetime import date

    item = _wht_evidence(scope, elected=True, amount=amount)
    # billing._seed_invoice issues at NOW (Africa/Lagos date 2026-08-17).
    return replace(
        item,
        determination=replace(item.determination, occurred_on=on or date(2026, 8, 17)),
    )


def _freeze_command(account_id, invoice_id, scope, evidence, command_id=None):
    from dotmac_billing.commands import FreezeIssuedInvoiceWithholding

    return FreezeIssuedInvoiceWithholding(
        scope,
        account_id,
        invoice_id,
        command_id or f"freeze:{uuid4()}",
        "finance-user:fixture",
        "invoice issued before withholding was frozen",
        evidence,
    )


def test_pre_freeze_invoice_is_frozen_then_discharged(scratch):
    from dotmac_billing.service import freeze_issued_invoice_withholding

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        # A legacy invoice: issued with no frozen component (like pre-bi_0016).
        account, _, invoice = billing._seed_invoice(session, scope, suffix="late")
        assert _components(session, invoice.id) == []
        evidence = _as_of_issue(scope)
        rows = freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(account.id, invoice.id, scope, evidence),
        )
        assert len(rows) == 1
        assert rows[0].origin == "late_freeze"
        assert rows[0].actor_ref == "finance-user:fixture"
        assert rows[0].tax_amount == Decimal("5.00")
        assert rows[0].tax_source_owner == "dotmac-tax"
        assert rows[0].determination_ref == str(
            evidence.determination.determination_set_id
        )
        # The normal discharge path now applies, unchanged.
        row = accept_withholding_discharge(
            session,
            scope=scope,
            command=_discharge(account, invoice.id, scope, evidence, accepted="5.00"),
        )
        assert row.amount_delta == Decimal("5.00")
        session.rollback()


def test_double_freeze_is_refused(scratch):
    from dotmac_billing.service import freeze_issued_invoice_withholding

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, _, invoice = billing._seed_invoice(session, scope, suffix="twice")
        evidence = _as_of_issue(scope)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(account.id, invoice.id, scope, evidence),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice.id, scope, _as_of_issue(scope)
                ),
            )
        assert refused.value.code == "billing.withholding_already_frozen"
        session.rollback()


def test_freeze_above_outstanding_is_refused(scratch):
    from dotmac_billing.service import freeze_issued_invoice_withholding

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        # The seeded invoice is 120.00; a 130.00 withholding cannot be frozen.
        account, _, invoice = billing._seed_invoice(session, scope, suffix="over")
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice.id, scope, _as_of_issue(scope, amount="130.00")
                ),
            )
        assert refused.value.code == "billing.withholding_freeze_exceeds_outstanding"
        assert _components(session, invoice.id) == []
        session.rollback()


def test_freeze_must_be_as_of_the_issue_date(scratch):
    from datetime import date

    from dotmac_billing.service import freeze_issued_invoice_withholding

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, _, invoice = billing._seed_invoice(session, scope, suffix="asof")
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id,
                    invoice.id,
                    scope,
                    _as_of_issue(scope, on=date(2026, 9, 30)),
                ),
            )
        assert refused.value.code == "billing.withholding_freeze_not_as_of_issue"
        session.rollback()


def test_replay_of_the_same_command_id_is_a_no_op(scratch):
    from dotmac_billing.service import freeze_issued_invoice_withholding

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, _, invoice = billing._seed_invoice(session, scope, suffix="replay")
        command = _freeze_command(
            account.id, invoice.id, scope, _as_of_issue(scope), "freeze:fixed-id"
        )
        first = freeze_issued_invoice_withholding(session, scope=scope, command=command)
        again = freeze_issued_invoice_withholding(session, scope=scope, command=command)
        assert [row.id for row in first] == [row.id for row in again]
        assert len(_components(session, invoice.id)) == 1
        session.rollback()


def test_platform_plane_freeze_is_refused(scratch):
    from dotmac_billing.commands import FreezeIssuedInvoiceWithholding
    from dotmac_billing.service import freeze_issued_invoice_withholding
    from dotmac_kernel.cache import PlatformScope

    _, _, platform_url = scratch
    scope = PlatformScope()
    tenant_evidence = _as_of_issue(TenantScope(uuid4()))
    with billing._session(platform_url, scope) as session:
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=FreezeIssuedInvoiceWithholding(
                    scope,
                    uuid4(),
                    uuid4(),
                    "freeze:platform",
                    "finance-user:fixture",
                    "platform stays fail-closed",
                    tenant_evidence,
                ),
            )
        assert refused.value.code == "billing.withholding_freeze_platform_unsupported"
