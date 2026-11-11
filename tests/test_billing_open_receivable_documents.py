"""Hosted PostgreSQL proofs for ``list_open_receivable_documents`` (G2).

The read returns only open, collectible invoices of one account and currency,
stays inside the caller's tenant (RLS) on both planes, and pages by a keyset.
"""

# EXPLAIN text below interpolates closed table literals only.
# ruff: noqa: S608
from uuid import uuid4

import pytest
from dotmac_billing import (
    OpenReceivableDocumentV1,
    list_open_receivable_documents,
)
from dotmac_billing.commands import AllocationCommand
from dotmac_billing.contracts import DueDateBasisStatus
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_rated_obligation,
    accept_settlement,
    allocate_settlement,
    create_billing_account,
    create_draft_document,
    issue_document,
    void_document,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, text

from tests import test_billing_isolation as b

CONFIRMED = frozenset({"provider_settled"})
NGN = Currency("NGN", 2)


@pytest.fixture(scope="module")
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _routing(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(b._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    return scope, tenant_url if plane == "tenant" else platform_url


def _account(session, scope):
    return create_billing_account(
        session,
        scope=scope,
        external_account_ref=f"customer-{uuid4()}",
        currency="NGN",
        minor_units=2,
    )


def _invoice(session, scope, account):
    obligation = accept_rated_obligation(
        session,
        scope=scope,
        command=b._obligation_command(
            account.id, scope=scope, source_fact_id=f"occurrence-{uuid4()}"
        ),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = create_draft_document(
        session, scope=scope, command=b._draft_command(obligation.id)
    )
    return issue_document(
        session, scope=scope, command=b._issue_command(draft.id), numbering=b.NUMBERING
    )


def _allocate(session, scope, account, invoice, amount):
    settlement = accept_settlement(
        session,
        scope=scope,
        command=b._settlement_command(
            account.id, scope=scope, source_key=str(uuid4()), amount=amount
        ),
        accepted_confirmation_evidence=CONFIRMED,
    )
    allocate_settlement(
        session,
        scope=scope,
        command=AllocationCommand(
            settlement.id, invoice.id, b._money(amount), b.NOW, str(uuid4())
        ),
    )


def _walk(session, scope, account_id, limit, currency=NGN):
    entries, cursor, pages = [], None, 0
    while True:
        page = list_open_receivable_documents(
            session,
            scope=scope,
            account=account_id,
            currency=currency,
            cursor=cursor,
            limit=limit,
        )
        pages += 1
        entries.extend(page.entries)
        cursor = page.next_cursor
        if cursor is None:
            return entries, pages
        assert pages < 50


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_reads_only_open_collectible_documents_and_paginates(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        other_account = _account(session, scope)
        opens = [_invoice(session, scope, account) for _ in range(4)]
        partial = _invoice(session, scope, account)
        resolved = _invoice(session, scope, account)
        voided = _invoice(session, scope, account)
        foreign = _invoice(session, scope, other_account)
        _allocate(session, scope, account, partial, "50.00")
        _allocate(session, scope, account, resolved, "120.00")
        void_document(
            session,
            scope=scope,
            document_id=voided.id,
            actor_ref="billing-test",
            occurred_at=b.NOW,
            source_ref=str(uuid4()),
        )
        session.commit()

        everything, _ = _walk(session, scope, account.id, 200)
        ids = [e.document_id for e in everything]
        assert set(ids) == {d.id for d in opens} | {partial.id}
        assert resolved.id not in ids and voided.id not in ids
        assert foreign.id not in ids
        assert ids == [
            e.document_id
            for e in sorted(everything, key=lambda e: (e.issued_at, e.document_id))
        ]
        by_id = {e.document_id: e for e in everything}
        assert all(isinstance(e, OpenReceivableDocumentV1) for e in everything)
        assert by_id[partial.id].financial_state == "partially_resolved"
        assert by_id[partial.id].collectible_receivable == b._money("70.00")
        first = by_id[opens[0].id]
        assert first.financial_state == "open"
        assert first.collectible_receivable == b._money("120.00")
        assert first.service_ref == "service:example"
        assert first.due_at == opens[0].due_at and first.due_at is not None
        assert first.issued_at == opens[0].issued_at
        assert first.source_version >= 1
        assert first.due_at_basis is DueDateBasisStatus.VERIFIED

        paged, pages = _walk(session, scope, account.id, 2)
        assert [e.document_id for e in paged] == ids
        assert pages >= 3

        only_foreign, _ = _walk(session, scope, other_account.id, 10)
        assert [e.document_id for e in only_foreign] == [foreign.id]


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_other_currency_and_unknown_account_return_nothing(scratch, plane):
    scope, url = _routing(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        _invoice(session, scope, account)
        session.commit()
        usd, _ = _walk(session, scope, account.id, 10, currency=Currency("USD", 2))
        assert usd == []
        nobody, _ = _walk(session, scope, uuid4(), 10)
        assert nobody == []


def test_tenant_isolation_and_rls(scratch):
    scope, url = _routing(scratch, "tenant")
    with b._session(url, scope) as session:
        account = _account(session, scope)
        invoice = _invoice(session, scope, account)
        session.commit()
        mine, _ = _walk(session, scope, account.id, 10)
        assert [e.document_id for e in mine] == [invoice.id]
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(url, other) as session:
        theirs, _ = _walk(session, other, account.id, 10)
        assert theirs == []
        # A tenant session addressed with another tenant's scope sees nothing:
        # the explicit tenant predicate and RLS both exclude the rows.
        crossed, _ = _walk(session, scope, account.id, 10)
        assert crossed == []


def test_read_does_not_mutate(scratch):
    scope, url = _routing(scratch, "tenant")
    with b._session(url, scope) as session:
        account = _account(session, scope)
        invoice = _invoice(session, scope, account)
        session.commit()
        before = list_open_receivable_documents(
            session, scope=scope, account=account.id, currency=NGN
        )
        after = list_open_receivable_documents(
            session, scope=scope, account=account.id, currency=NGN
        )
        assert before == after
        assert [e.document_id for e in before.entries] == [invoice.id]


def test_invalid_limit_is_refused(scratch):
    scope, url = _routing(scratch, "tenant")
    with b._session(url, scope) as session:
        with pytest.raises(BillingRuleViolation):
            list_open_receivable_documents(
                session, scope=scope, account=uuid4(), currency=NGN, limit=0
            )


def test_index_exists_on_both_planes_and_serves_the_read(scratch):
    admin_url = scratch[0]
    engine = create_engine(admin_url)
    expected = {
        "ix_documents_open_by_account": (
            "documents",
            ["tenant_id", "billing_account_id", "issued_at", "id"],
        ),
        "ix_platform_documents_open_by_account": (
            "platform_documents",
            ["billing_account_id", "issued_at", "id"],
        ),
    }
    try:
        with engine.connect() as connection:
            for name, (table, columns) in expected.items():
                row = connection.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname = 'mod_billing' AND tablename = :t "
                        "AND indexname = :n"
                    ),
                    {"t": table, "n": name},
                ).scalar_one()
                assert f"({', '.join(columns)})" in row
                assert "document_kind" in row and "issued_at IS NOT NULL" in row
        # The planner may prefer a scan on an empty table; forbid a sequential
        # scan so the plan shows the index is usable for the read's predicate.
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL enable_seqscan = off"))
            for name, (table, columns) in expected.items():
                tenant = "tenant_id = :tenant AND " if "tenant_id" in columns else ""
                plan = "\n".join(
                    connection.execute(
                        text(
                            "EXPLAIN SELECT issued_at, id FROM "
                            f"mod_billing.{table} WHERE {tenant}"
                            "billing_account_id = :account "
                            "AND document_kind = 'invoice' "
                            "AND issued_at IS NOT NULL AND currency = 'NGN' "
                            "AND minor_units = 2 ORDER BY issued_at, id LIMIT 10"
                        ),
                        {"tenant": uuid4(), "account": uuid4()},
                    ).scalars()
                )
                assert name in plan, plan
    finally:
        engine.dispose()
