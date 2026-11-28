"""Hosted PostgreSQL canaries for the a16 withholding side refusal and record read.

Refusals happen before any write. Stored rows (which the validators still
decode) are read back through ``get_withholding_discharge_record``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import get_withholding_discharge, get_withholding_discharge_record
from dotmac_billing.commands import ReverseWithholdingDischarge
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_rated_obligation,
    accept_withholding_discharge,
    create_billing_account,
    create_draft_document,
    issue_document,
    reverse_withholding_discharge,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests import test_billing_isolation as billing
from tests import test_billing_public_invoice_authoring as authoring
from tests import test_billing_tax_determination_evidence as evidence_fixtures
from tests import test_billing_withholding_frozen as frozen
from tests.billing_frozen_fixtures import (
    withholding_command as frozen_withholding_command,
)

CODE = "billing.withholding_transaction_side_invalid"


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _count(session, table):
    return session.execute(text(f"SELECT count(*) FROM mod_billing.{table}")).scalar()  # noqa: S608


def test_intake_refuses_expected_withholding_on_a_vat_side(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        item = evidence_fixtures._evidence(
            scope, purpose="expected_withholding", taxes=("5.00",), side="output"
        )
        with pytest.raises(BillingRuleViolation) as refused:
            authoring._issue(
                session,
                scope,
                evidence_fixtures._command(account.id, scope, (item,)),
            )
        assert refused.value.code == CODE
        assert _count(session, "rated_obligations") == 0
        session.rollback()


def test_freeze_command_refuses_a_non_withholding_side(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, tax = frozen._v2_invoice(session, scope)
        item = frozen._late(session, invoice_id, tax)
        bad = replace(
            item, determination=replace(item.determination, transaction_side="output")
        )
        with pytest.raises(BillingRuleViolation) as refused:
            frozen.freeze_issued_invoice_withholding(
                session,
                scope=scope,
                command=frozen._freeze_command(account.id, invoice_id, scope, bad),
            )
        assert refused.value.code == CODE
        assert frozen._components(session, invoice_id) == []
        session.rollback()


def test_discharge_refuses_a_non_withholding_side(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        command = frozen._discharge(
            account, invoice_id, scope, evidence, transaction_side="sale"
        )
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(session, scope=scope, command=command)
        assert refused.value.code == CODE
        assert _count(session, "withholding_discharges") == 0
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_record_read_both_planes_with_reversal(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(billing._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(
            session, scope, suffix=f"rec-{plane}"
        )
        command = frozen_withholding_command(
            session, account.id, invoice.id, scope=scope, suffix=plane, accepted="3.00"
        )
        row = accept_withholding_discharge(session, scope=scope, command=command)
        session.flush()
        record = get_withholding_discharge_record(
            session, scope=scope, discharge_id=row.id
        )
        assert record.discharge_id == row.id
        assert record.billing_account_id == account.id
        assert record.document_id == invoice.id
        assert record.posting_group_id == row.posting_group_id
        assert record.reverses_discharge_id is None
        assert record.recorded_at == row.created_at
        assert record.occurred_at == command.occurred_at
        assert record.effect.accepted_delta.amount == Decimal("3.00")

        inverse = reverse_withholding_discharge(
            session,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                row.id,
                "rec-undo-retry",
                "rec-undo",
                "finance-user:fixture",
                "audit:undo",
                billing.NOW,
            ),
        )
        session.flush()
        undone = get_withholding_discharge_record(
            session, scope=scope, discharge_id=inverse.id
        )
        assert undone.reverses_discharge_id == row.id
        assert undone.effect.accepted_delta.amount == Decimal("-3.00")
        assert undone.recorded_at >= record.recorded_at

        with pytest.raises(BillingRuleViolation) as missing:
            get_withholding_discharge_record(session, scope=scope, discharge_id=uuid4())
        assert missing.value.code == "billing.not_found"
        session.rollback()


def test_record_read_foreign_tenant_gets_not_found(scratch):
    admin_url, tenant_url, _ = scratch
    left, right = billing._create_tenants(admin_url, 2)
    owner, other = TenantScope(left), TenantScope(right)
    with billing._session(tenant_url, owner) as session:
        account, _, invoice = billing._seed_invoice(
            session, owner, suffix="rec-foreign"
        )
        command = frozen_withholding_command(
            session, account.id, invoice.id, scope=owner, suffix="foreign"
        )
        row = accept_withholding_discharge(session, scope=owner, command=command)
        session.commit()
    with billing._session(tenant_url, other) as session:
        with pytest.raises(BillingRuleViolation) as missing:
            get_withholding_discharge_record(session, scope=other, discharge_id=row.id)
        assert missing.value.code == "billing.not_found"


def _tamper(url, statement, **params):
    """Plant a stored wrong-side row past the append-only guards (test only)."""
    engine = create_engine(
        billing._url_for(billing._superuser_url(), make_url(url).database)
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(text(statement), params)
    finally:
        engine.dispose()


def test_stored_wrong_side_discharge_still_reads_and_reverses(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        command = frozen._discharge(account, invoice_id, scope, evidence)
        row = accept_withholding_discharge(session, scope=scope, command=command)
        discharge_id = row.id
        session.commit()
    _tamper(
        tenant_url,
        "UPDATE mod_billing.withholding_discharges SET evidence = CAST("
        "jsonb_set(CAST(evidence AS jsonb), '{tax_evidence,transaction_side}', "
        "'\"sale\"') AS json) WHERE id = :id",
        id=discharge_id,
    )
    with billing._session(tenant_url, scope) as session:
        effect = get_withholding_discharge(
            session, scope=scope, discharge_id=discharge_id
        )
        assert effect.tax_evidence.transaction_side == "sale"
        record = get_withholding_discharge_record(
            session, scope=scope, discharge_id=discharge_id
        )
        assert record.effect.tax_evidence.transaction_side == "sale"
        inverse = reverse_withholding_discharge(
            session,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                discharge_id,
                "side-undo-retry",
                "side-undo",
                "finance-user:fixture",
                "audit:undo",
                billing.NOW,
            ),
        )
        assert inverse.reverses_discharge_id == discharge_id
        session.rollback()


class _CountingNumbering:
    def __init__(self):
        self.calls = 0

    def allocate(self, db, **kw):
        self.calls += 1
        return billing.NUMBERING.allocate(db, **kw)


def test_stored_wrong_side_obligation_is_refused_at_issue_before_any_write(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    with billing._session(tenant_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        item = evidence_fixtures._evidence(
            scope, purpose="expected_withholding", taxes=("5.00",)
        )
        command = evidence_fixtures._command(account.id, scope, (item,)).obligation
        obligation = accept_rated_obligation(
            session,
            scope=scope,
            command=command,
            accepted_source_kinds=frozenset({command.source_kind}),
        )
        draft = create_draft_document(
            session, scope=scope, command=billing._draft_command(obligation.id)
        )
        account_id, obligation_id, document_id = account.id, obligation.id, draft.id
        session.commit()
    _tamper(
        tenant_url,
        "UPDATE mod_billing.rated_obligations SET tax_evidence = CAST("
        "jsonb_set(CAST(tax_evidence AS jsonb), '{0,determination,"
        "transaction_side}', '\"output\"') AS json) WHERE id = :id",
        id=obligation_id,
    )
    numbering = _CountingNumbering()
    with billing._session(tenant_url, scope) as session:
        with pytest.raises(BillingRuleViolation) as refused:
            issue_document(
                session,
                scope=scope,
                command=billing._issue_command(document_id),
                numbering=numbering,
            )
        assert refused.value.code == CODE
        session.rollback()
    assert numbering.calls == 0
    with billing._session(tenant_url, scope) as session:
        issued, number = session.execute(
            text(
                "SELECT issued_at, document_number FROM mod_billing.documents "
                "WHERE id = :d"
            ),
            {"d": document_id},
        ).one()
        assert issued is None and number is None
        for table in ("document_withholding_components", "posting_groups"):
            assert (
                session.execute(
                    text(
                        f"SELECT count(*) FROM mod_billing.{table} "  # noqa: S608
                        "WHERE billing_account_id = :a"
                    ),
                    {"a": account_id},
                ).scalar()
                == 0
            )
        session.rollback()
