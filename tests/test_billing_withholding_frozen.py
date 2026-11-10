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
    WITHHOLDING_FREEZE_PERMISSION,
    ApplyAvailableCreditCommand,
    ReverseWithholdingDischarge,
    TaxDeterminationSealV1,
    TaxSealedComponentV1,
    TaxSealedDependencyV1,
    TaxSealedLineV1,
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
from tests import test_billing_tax_dependency_evidence as dependency
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


def _sealed(item):
    """What dotmac-tax would seal for this evidence (the double reads the evidence)."""
    result = item.determination
    return TaxDeterminationSealV1(
        determination_set_id=result.determination_set_id,
        result_fingerprint=result.result_fingerprint,
        source_fingerprint=result.source_fingerprint,
        occurred_on=result.occurred_on,
        determined_at=result.determined_at,
        tenant_id=result.tenant_id,
        commercial_source=result.commercial_source,
        source_amount=result.source_amount,
        recognition_basis_code=result.recognition_basis_code,
        counterparty_ref=result.counterparty_ref,
        supply_ref=result.supply_ref,
        place_ref=result.place_ref,
        components=tuple(
            TaxSealedComponentV1(
                determination_id=c.determination_id,
                tax_code_id=c.tax_code_id,
                rule_id=c.rule_id,
                rule_version=c.rule_version,
                treatment_code=c.treatment_code,
                calculation_base_code=c.calculation_base_code,
                party_category=c.party_category,
                party_classification_id=c.party_classification_id,
                base_amount=c.base_amount,
                tax_amount=c.tax_amount,
                lines=tuple(
                    TaxSealedLineV1(
                        ln.sequence, ln.taxable_amount, ln.rate, ln.tax_amount
                    )
                    for ln in c.lines
                ),
                dependency_components=tuple(
                    TaxSealedDependencyV1(
                        d.determination_set_id, d.determination_id, d.tax_amount
                    )
                    for d in c.dependency_components
                ),
            )
            for c in result.components
            if c.tax_amount.amount > 0 and c.party_classification_id is not None
        ),
    )


class _TaxSeals:
    """A dotmac-tax reader double holding the sealed result of what it is given."""

    def __init__(self, *items):
        self.items = {
            item.determination.determination_set_id: _sealed(item) for item in items
        }

    def read_seal(self, *, scope, determination_set_id):
        return self.items.get(determination_set_id)


class _Permissions:
    """A permission reader double: holds the freeze permission for named actors."""

    def __init__(self, *actors):
        self.actors = set(actors)

    def holds(self, *, scope, actor_ref, code):
        return code == WITHHOLDING_FREEZE_PERMISSION and actor_ref in self.actors


def freeze_issued_invoice_withholding(
    session, *, scope, command, tax_seal="confirm", permissions="grant"
):
    """The service command with a confirming Tax reader and a granting reader."""
    from dotmac_billing import service as billing_service

    if isinstance(tax_seal, str):
        tax_seal = _TaxSeals(command.evidence)
    if isinstance(permissions, str):
        permissions = _Permissions(command.actor_ref)
    return billing_service.freeze_issued_invoice_withholding(
        session,
        scope=scope,
        command=command,
        tax_seal=tax_seal,
        permissions=permissions,
    )


def _v2_invoice(session, scope):
    """An invoice issued with V2 Tax evidence (and so declared line sources)
    whose expected withholding was not elected at issue: nothing was frozen."""
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=2,
    )
    tax = dependency._evidence(scope)
    issued = authoring._issue(
        session, scope, dependency._command(account.id, scope, tax)
    )
    session.flush()
    return account, issued.invoice_id, tax


def _v2_discharge(account, invoice_id, scope, item, *, accepted="5.00"):
    """A discharge command citing a V2 (dependency-lineage) determination."""
    old = billing._withholding_command(
        account.id, invoice_id, scope=scope, suffix=str(uuid4())
    )
    return replace(
        old,
        tax_evidence=dependency._discharge_evidence(
            scope, account.id, invoice_id, item
        ),
        claimed_amount=_money(accepted),
        accepted_amount=_money(accepted),
    )


def _issue_date(session, invoice_id):
    return session.execute(
        text(
            "SELECT (issued_at AT TIME ZONE timezone)::date "
            "FROM mod_billing.documents WHERE id = :d"
        ),
        {"d": invoice_id},
    ).scalar_one()


def _late(session, invoice_id, tax, **changes):
    """The invoice's own expected-withholding Tax result, re-determined as of
    its issue date with the party election (what finance would obtain from Tax)."""
    item = tax[1]
    on = _issue_date(session, invoice_id)
    result = item.determination
    component = replace(
        result.components[0],
        party_category="wht-elected",
        party_classification_id=uuid4(),
    )
    result = replace(result, components=(component,), occurred_on=on)
    priors = tuple(replace(p, occurred_on=on) for p in item.prior_determinations)
    item = replace(item, determination=result, prior_determinations=priors)
    return replace(item, **changes) if changes else item


def _line_obligation(session, invoice_id):
    return session.execute(
        text(
            "SELECT obligation_id FROM mod_billing.document_lines "
            "WHERE document_id = :d"
        ),
        {"d": invoice_id},
    ).scalar_one()


def test_pre_freeze_invoice_is_frozen_then_discharged(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        # An invoice issued with no frozen component (like pre-bi_0016).
        account, invoice_id, tax = _v2_invoice(session, scope)
        assert _components(session, invoice_id) == []
        evidence = _late(session, invoice_id, tax)
        rows = freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(account.id, invoice_id, scope, evidence),
        )
        assert len(rows) == 1
        assert rows[0].origin == "late_freeze"
        assert rows[0].actor_ref == "finance-user:fixture"
        assert rows[0].tax_amount == Decimal("10.00")
        assert rows[0].tax_source_owner == "dotmac-tax"
        # The row carries the bound line's obligation, not a caller choice.
        assert rows[0].obligation_id == _line_obligation(session, invoice_id)
        assert rows[0].determination_ref == str(
            evidence.determination.determination_set_id
        )
        # The normal discharge path now applies, unchanged.
        row = accept_withholding_discharge(
            session,
            scope=scope,
            command=_v2_discharge(account, invoice_id, scope, evidence),
        )
        assert row.amount_delta == Decimal("5.00")
        session.rollback()


def test_double_freeze_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        evidence = _late(session, invoice_id, tax)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(account.id, invoice_id, scope, evidence),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _late(session, invoice_id, tax)
                ),
            )
        assert refused.value.code == "billing.withholding_already_frozen"
        session.rollback()


def test_freeze_above_outstanding_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        # The invoice is 107.50; a 130.00 withholding cannot be frozen.
        account, invoice_id, _ = _v2_invoice(session, scope)
        big = dependency._evidence(scope, wht_tax="130")
        # Same source and original facts as the invoice's own line.
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _late(session, invoice_id, big)
                ),
            )
        assert refused.value.code == "billing.withholding_freeze_exceeds_outstanding"
        assert _components(session, invoice_id) == []
        session.rollback()


def test_freeze_must_be_as_of_the_issue_date(scratch):
    from datetime import date

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        late = _late(session, invoice_id, tax)
        other = date(2026, 9, 30)
        late = replace(
            late,
            determination=replace(late.determination, occurred_on=other),
            prior_determinations=tuple(
                replace(p, occurred_on=other) for p in late.prior_determinations
            ),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(account.id, invoice_id, scope, late),
            )
        assert refused.value.code == "billing.withholding_freeze_not_as_of_issue"
        session.rollback()


def test_replay_of_the_same_command_id_is_a_no_op(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        command = _freeze_command(
            account.id,
            invoice_id,
            scope,
            _late(session, invoice_id, tax),
            "freeze:fixed-id",
        )
        first = freeze_issued_invoice_withholding(session, scope=scope, command=command)
        again = freeze_issued_invoice_withholding(session, scope=scope, command=command)
        assert [row.id for row in first] == [row.id for row in again]
        assert len(_components(session, invoice_id)) == 1
        session.rollback()


def test_v1_evidence_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, _ = _v2_invoice(session, scope)
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _as_of_issue(scope)
                ),
            )
        assert refused.value.code == "billing.tax_evidence_invalid"
        session.rollback()


def _retarget(late, **result_changes):
    """Apply a change to the principal result and every prior (one source)."""
    return replace(
        late,
        determination=replace(late.determination, **result_changes),
        prior_determinations=tuple(
            replace(p, **{k: v for k, v in result_changes.items() if k != "components"})
            for p in late.prior_determinations
        ),
    )


def test_a_determination_for_another_commercial_source_is_refused(scratch):
    from dotmac_billing.tax_dependency_evidence import TaxCommercialSourceEvidenceV1

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        late = _retarget(
            _late(session, invoice_id, tax),
            commercial_source=TaxCommercialSourceEvidenceV1("commercial:invented", "1"),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(account.id, invoice_id, scope, late),
            )
        assert refused.value.code == "billing.withholding_freeze_unbound"
        assert _components(session, invoice_id) == []
        session.rollback()


def test_a_determination_with_other_original_facts_is_refused(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        late = _retarget(
            _late(session, invoice_id, tax), counterparty_ref="another-customer"
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(account.id, invoice_id, scope, late),
            )
        assert refused.value.code == "billing.withholding_freeze_facts_differ"
        assert _components(session, invoice_id) == []
        session.rollback()


def test_a_base_above_the_bound_line_is_refused(scratch):
    """The Tax component contract pins a dependency base to source plus prior tax.

    A base of 500.00 on a 100.00 source with 7.50 of prior tax is refused when the
    evidence is built, before the service could ever see it; the service then
    compares the (valid) base with the bound line's source amount as a second guard.
    """
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, invoice_id, tax = _v2_invoice(session, scope)
        late = _late(session, invoice_id, tax)
        component = late.determination.components[0]
        component = replace(
            component,
            base_amount=_money("500.00"),
            lines=(replace(component.lines[0], taxable_amount=_money("500.00")),),
        )
        with pytest.raises(ValueError, match="dependency base differs"):
            replace(
                late,
                determination=replace(late.determination, components=(component,)),
            )
        assert _components(session, invoice_id) == []
        session.rollback()


def test_withholding_already_accepted_under_old_rules_does_not_block_a_freeze(
    scratch, monkeypatch
):
    """Older records stay correctable: outstanding plus already-withheld bounds it."""
    from alembic import command as alembic_command
    from alembic.config import Config

    with billing._scratch_database(
        (ModulePlane.TENANT,), target_revision="bi_0015_line_price_version_fk"
    ) as (history_admin, history_tenant, _):
        scope = _scope(history_admin)
        monkeypatch.setattr(
            service, "_require_frozen_withholding", lambda *a, **k: None
        )
        with billing._session(history_tenant, scope) as session:
            account, invoice_id, tax = _v2_invoice(session, scope)
            account_id = account.id
            # 4.00 accepted before components existed; outstanding is now 103.50.
            accept_withholding_discharge(
                session,
                scope=scope,
                command=_v2_discharge(
                    account, invoice_id, scope, tax[1], accepted="4.00"
                ),
            )
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
        monkeypatch.undo()
        with billing._session(history_tenant, scope) as session:
            # 105.00 exceeds the 103.50 outstanding but not outstanding + 4.00.
            big = dependency._evidence(scope, wht_tax="105")
            rows = freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account_id, invoice_id, scope, _late(session, invoice_id, big)
                ),
            )
            assert [row.tax_amount for row in rows] == [Decimal("105.00")]
            session.rollback()


def _extra_row(session, scope, invoice_id, origin, obligation_id=None):
    """Another component row written straight to the table (the app role can)."""
    plane = service._models(scope)
    document = session.get(plane.document, invoice_id)
    result = evidence_fixtures._evidence(
        scope, purpose="expected_withholding", taxes=("1.00",)
    ).determination
    component = replace(result.components[0], party_classification_id=uuid4())
    session.add(
        service._frozen_component_row(
            scope=scope,
            plane=plane,
            document=document,
            obligation_id=obligation_id,
            result=result,
            component=component,
            origin=origin,
        )
    )
    session.flush()


_LATE = {
    "origin": "late_freeze",
    "actor_ref": "finance-user:fixture",
    "reason": "fixture: a second freeze",
}


def test_database_refuses_a_late_freeze_next_to_issue_rows(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, invoice_id, _ = _invoice(session, scope, elected=True)
        assert len(_components(session, invoice_id)) == 1
        with pytest.raises(DBAPIError) as refused:
            _extra_row(
                session,
                scope,
                invoice_id,
                {**_LATE, "command_id": f"late:{uuid4()}"},
            )
        assert refused.value.orig.sqlstate == "23514"
        session.rollback()


def test_database_refuses_a_second_freeze_with_another_command(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(
                account.id, invoice_id, scope, _late(session, invoice_id, tax)
            ),
        )
        with pytest.raises(DBAPIError) as refused:
            _extra_row(
                session,
                scope,
                invoice_id,
                {**_LATE, "command_id": f"other:{uuid4()}"},
            )
        assert refused.value.orig.sqlstate == "23514"
        session.rollback()


def _late_row(session, scope, invoice_id, sealed, command_id=None):
    """A late-freeze component row for ``invoice_id`` built from a sealed result."""
    plane = service._models(scope)
    document = session.get(plane.document, invoice_id)
    session.add(
        service._frozen_component_row(
            scope=scope,
            plane=plane,
            document=document,
            obligation_id=None,
            result=sealed,
            component=sealed.components[0],
            origin={
                **_LATE,
                "command_id": command_id or f"late:{uuid4()}",
            },
        )
    )
    session.flush()


def test_database_refuses_one_determination_on_a_second_live_invoice(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        first_account, first_id, tax = _v2_invoice(session, scope)
        _, second_id, _ = _v2_invoice(session, scope)
        late = _late(session, first_id, tax)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(first_account.id, first_id, scope, late),
        )
        with pytest.raises(DBAPIError) as refused:
            _late_row(session, scope, second_id, _sealed(late))
        assert refused.value.orig.sqlstate == "23505"
        session.rollback()


def test_a_voided_invoice_releases_its_determination(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        first_account, first_id, tax = _v2_invoice(session, scope)
        second_account, second_id, _ = _v2_invoice(session, scope)
        late = _late(session, first_id, tax)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(first_account.id, first_id, scope, late),
        )
        service.void_document(
            session,
            scope=scope,
            document_id=first_id,
            actor_ref="finance-user:fixture",
            occurred_at=billing.NOW,
            source_ref=str(uuid4()),
        )
        # Allowed by the database and by the service once the first is voided.
        _late_row(session, scope, second_id, _sealed(late))
        assert len(_components(session, second_id)) == 1
        session.rollback()


def test_concurrent_freezes_of_one_determination_serialize(scratch):
    import threading
    import time

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, first_id, tax = _v2_invoice(session, scope)
        _, second_id, _ = _v2_invoice(session, scope)
        sealed = _sealed(_late(session, first_id, tax))
        session.commit()
    outcome = []
    started = threading.Event()

    def second():
        try:
            with billing._session(tenant_url, scope) as other:
                started.set()
                _late_row(other, scope, second_id, sealed)
                other.commit()
                outcome.append("committed")
        except DBAPIError as error:
            outcome.append(error.orig.sqlstate)

    with billing._session(tenant_url, scope) as holder:
        _late_row(holder, scope, first_id, sealed)
        thread = threading.Thread(target=second, daemon=True)
        thread.start()
        assert started.wait(timeout=30)
        time.sleep(1.0)
        # The second freeze waits on the determination lock the first holds.
        assert outcome == [] and thread.is_alive()
        holder.commit()
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert outcome == ["23505"]
    with billing._session(tenant_url, scope) as check:
        assert len(_components(check, first_id)) == 1
        assert _components(check, second_id) == []


def test_database_refuses_a_component_on_another_documents_line(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        _, invoice_id, _ = _v2_invoice(session, scope)
        _, other_id, _ = _v2_invoice(session, scope)
        with pytest.raises(DBAPIError) as refused:
            _extra_row(
                session,
                scope,
                invoice_id,
                {**_LATE, "command_id": f"late:{uuid4()}"},
                obligation_id=_line_obligation(session, other_id),
            )
        assert refused.value.orig.sqlstate == "23503"
        session.rollback()


def test_no_tax_reader_wired_refuses_the_freeze(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _late(session, invoice_id, tax)
                ),
                tax_seal=None,
            )
        assert refused.value.code == "billing.withholding_freeze_tax_seal_unavailable"
        assert _components(session, invoice_id) == []
        session.rollback()


@pytest.mark.parametrize(
    "fault", ["unknown", "fingerprint", "date", "amount", "dependency"]
)
def test_tax_must_confirm_the_determination(scratch, fault):
    from datetime import timedelta

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        evidence = _late(session, invoice_id, tax)
        seals = _TaxSeals(evidence)
        key = evidence.determination.determination_set_id
        if fault == "unknown":
            seals.items.clear()
        elif fault == "amount":
            # A real set id and fingerprint paired with a different amount.
            seal = seals.items[key]
            seals.items[key] = replace(
                seal,
                components=(replace(seal.components[0], tax_amount=_money("1.00")),),
            )
        elif fault == "dependency":
            # Tax sealed a smaller prior tax than the caller's evidence offers.
            seal = seals.items[key]
            component = seal.components[0]
            assert component.dependency_components
            seals.items[key] = replace(
                seal,
                components=(
                    replace(
                        component,
                        dependency_components=tuple(
                            replace(d, tax_amount=_money("1.00"))
                            for d in component.dependency_components
                        ),
                    ),
                ),
            )
        elif fault == "fingerprint":
            seals.items[key] = replace(
                seals.items[key], result_fingerprint="rv2:" + "c" * 64
            )
        else:
            seals.items[key] = replace(
                seals.items[key],
                occurred_on=seals.items[key].occurred_on + timedelta(days=1),
            )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(account.id, invoice_id, scope, evidence),
                tax_seal=seals,
            )
        assert refused.value.code == "billing.withholding_freeze_tax_seal_mismatch"
        assert _components(session, invoice_id) == []
        session.rollback()


def test_no_permission_reader_wired_refuses_the_freeze(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _late(session, invoice_id, tax)
                ),
                permissions=None,
            )
        assert refused.value.code == "billing.withholding_freeze_permission_unavailable"
        assert _components(session, invoice_id) == []
        session.rollback()


def test_an_actor_without_the_permission_cannot_freeze(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = _v2_invoice(session, scope)
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    account.id, invoice_id, scope, _late(session, invoice_id, tax)
                ),
                permissions=_Permissions("someone-else"),
            )
        assert refused.value.code == "billing.withholding_freeze_permission_denied"
        assert _components(session, invoice_id) == []
        session.rollback()


def _line_id(session, invoice_id):
    return session.execute(
        text("SELECT id FROM mod_billing.document_lines WHERE document_id = :d"),
        {"d": invoice_id},
    ).scalar_one()


def test_a_v1_era_line_is_frozen_only_when_named_and_confirmed(scratch):
    from dataclasses import replace as _replace

    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        # Issued with V1 retained Tax evidence: no commercial source on the line.
        account, invoice_id, _ = _invoice(session, scope, elected=False)
        late = _late(session, invoice_id, dependency._evidence(scope))
        unnamed = _freeze_command(account.id, invoice_id, scope, late)
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(session, scope=scope, command=unnamed)
        assert refused.value.code == "billing.withholding_freeze_unbound"
        named = _replace(unnamed, line_id=_line_id(session, invoice_id))
        # Tax must confirm the commercial source and facts for that line.
        seals = _TaxSeals(late)
        key = late.determination.determination_set_id
        seals.items[key] = _replace(
            seals.items[key], counterparty_ref="another-customer"
        )
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session, scope=scope, command=named, tax_seal=seals
            )
        assert refused.value.code == "billing.withholding_freeze_tax_seal_mismatch"
        rows = freeze_issued_invoice_withholding(session, scope=scope, command=named)
        assert [row.obligation_id for row in rows] == [
            _line_obligation(session, invoice_id)
        ]
        session.rollback()


def test_one_determination_cannot_back_two_live_invoices(scratch):
    admin_url, tenant_url, _ = scratch
    scope = _scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        first_account, first_id, tax = _v2_invoice(session, scope)
        second_account, second_id, _ = _v2_invoice(session, scope)
        freeze_issued_invoice_withholding(
            session,
            scope=scope,
            command=_freeze_command(
                first_account.id, first_id, scope, _late(session, first_id, tax)
            ),
        )
        # The same sealed determination, offered for a different live invoice.
        with pytest.raises(BillingRuleViolation) as refused:
            freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=_freeze_command(
                    second_account.id, second_id, scope, _late(session, second_id, tax)
                ),
            )
        assert refused.value.code == "billing.withholding_freeze_determination_in_use"
        assert _components(session, second_id) == []
        session.rollback()


def test_platform_plane_freeze_is_refused(scratch):
    from dotmac_billing.commands import FreezeIssuedInvoiceWithholding
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
