"""Hosted PostgreSQL proofs for every issued Billing document's line closure."""

from __future__ import annotations

# Fixed plane identifiers; all values are bound.
# ruff: noqa: S608
import threading
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from dotmac_billing import models
from dotmac_billing.commands import IssueCreditNote
from dotmac_billing.contracts import (
    DOCUMENT_FACT_CONTRACT,
    InvoiceDocumentFactV1,
    InvoiceLineFactV1,
)
from dotmac_billing.service import _jsonable, issue_credit_note, issue_document
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _routing(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(billing._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    return scope, tenant_url if plane == "tenant" else platform_url


def _credit_command(invoice_id):
    return IssueCreditNote(
        original_document_id=invoice_id,
        pre_tax_amount=billing._money("20"),
        tax_amount=billing._money("0"),
        total_amount=billing._money("20"),
        reason="service correction",
        series_code="credit",
        reference_date=date(2026, 8, 17),
        actor_ref="billing-test",
        correlation_id=f"credit:{invoice_id}",
        occurred_at=billing.NOW,
        document_profile_code="credit.standard",
        document_profile_version="1",
    )


def _append_line(connection, *, plane, scope, document_id, account_id):
    prefix = "" if plane == "tenant" else "platform_"
    tenant_column = "tenant_id," if plane == "tenant" else ""
    tenant_value = ":tenant_id," if plane == "tenant" else ""
    connection.execute(
        text(f"""INSERT INTO mod_billing.{prefix}document_lines
        ({tenant_column}id,document_id,billing_account_id,line_number,
         description,quantity,unit_code,unit_amount,pre_tax_amount,tax_amount,
         total_amount,currency,minor_units,price_source_version)
        VALUES ({tenant_value}:id,:document_id,:account_id,2,
         'additional line',1,'credit',20,20,0,20,'NGN',2,'credit:test')"""),
        {
            "id": uuid.uuid4(),
            "document_id": document_id,
            "account_id": account_id,
            "tenant_id": scope.tenant_id if plane == "tenant" else None,
        },
    )


def _draft(session, scope, kind, *, account_id=None):
    if account_id is None:
        account_id = billing.create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"line-closure:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        ).id
    obligation = billing.accept_rated_obligation(
        session,
        scope=scope,
        command=billing._obligation_command(
            account_id, scope=scope, source_fact_id=f"line-closure:{uuid.uuid4()}"
        ),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = billing.create_draft_document(
        session, scope=scope, command=billing._draft_command(obligation.id)
    )
    # Typed ORM factory for the generic receipt kind supported by fact V1.
    # This is fixture setup, not a new public receipt-authoring command.
    draft.document_kind = kind
    session.flush()
    return account_id, draft.id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_issue_freezes_initial_line_and_fact_and_refuses_owner_append(
    scratch, plane
):
    scope, url = _routing(scratch, plane)
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    line_model = (
        models.DocumentLine if plane == "tenant" else models.PlatformDocumentLine
    )
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(
            session, scope, suffix=str(uuid.uuid4())
        )
        command = _credit_command(invoice.id)
        credit = issue_credit_note(
            session, scope=scope, command=command, numbering=billing.NUMBERING
        )
        session.commit()
        credit_id, account_id = credit.id, account.id
        assert credit.lifecycle == "issued"
        assert credit.issued_at == billing.NOW
        assert credit.document_number is not None
        line = session.scalars(
            select(line_model).where(line_model.document_id == credit.id)
        ).one()
        fact = session.scalars(
            select(fact_model).where(fact_model.document_id == credit.id)
        ).one()
        before_payload = fact.fact_payload
        before_digest = fact.presentation_model_digest
        assert line.total_amount == Decimal("20")
        assert len(before_payload["lines"]) == 1
        assert Decimal(before_payload["lines"][0]["total_amount"]["amount"]) == Decimal(
            "20"
        )
        assert (
            issue_credit_note(
                session, scope=scope, command=command, numbering=billing.NUMBERING
            ).id
            == credit_id
        )

    # The migration owner has INSERT privileges; the trigger, rather than a
    # privilege denial or an ORM listener, must enforce closure.
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            _append_line(
                connection,
                plane=plane,
                scope=scope,
                document_id=credit_id,
                account_id=account_id,
            )
        assert error.value.orig.sqlstate == "23001"
    engine.dispose()
    with billing._session(url, scope) as session:
        fact = session.scalars(
            select(fact_model).where(fact_model.document_id == credit_id)
        ).one()
        assert fact.fact_payload == before_payload
        assert fact.presentation_model_digest == before_digest
        assert (
            len(
                session.scalars(
                    select(line_model).where(line_model.document_id == credit_id)
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["invoice", "credit_note", "receipt"])
def test_every_issued_kind_refuses_even_its_first_line(scratch, plane, kind):
    scope, url = _routing(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    with billing._session(url, scope) as session:
        account_id, document_id = _draft(session, scope, kind)
        session.commit()
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        # Construct malformed issued evidence deliberately: absence of an
        # initial line must never become an exception to immutability.
        connection.execute(
            text(
                f"DELETE FROM mod_billing.{prefix}document_lines WHERE document_id=:id"
            ),
            {"id": document_id},
        )
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents SET lifecycle='issued', "
                "issued_at=:at, document_number=:number WHERE id=:id"
            ),
            {"id": document_id, "at": billing.NOW, "number": str(uuid.uuid4())},
        )
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            _append_line(
                connection,
                plane=plane,
                scope=scope,
                document_id=document_id,
                account_id=account_id,
            )
        assert error.value.orig.sqlstate == "23001"
        # This deliberately malformed header is never admitted as financial
        # history. Roll it back after the immediate line-immutability probe;
        # independent deferred issuance completeness must still reject it.
        connection.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["invoice", "credit_note", "receipt"])
def test_owner_cannot_reparent_a_draft_line_into_an_issued_document(
    scratch, plane, kind
):
    scope, url = _routing(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(
            session, scope, suffix=str(uuid.uuid4())
        )
        target = invoice
        if kind == "credit_note":
            target = issue_credit_note(
                session,
                scope=scope,
                command=_credit_command(invoice.id),
                numbering=billing.NUMBERING,
            )
        elif kind == "receipt":
            _, target_id = _draft(session, scope, "receipt", account_id=account.id)
            target = issue_document(
                session,
                scope=scope,
                command=billing._issue_command(target_id),
                numbering=billing.NUMBERING,
            )
        _, source_id = _draft(session, scope, "receipt", account_id=account.id)
        target_id = target.id
        session.commit()
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    f"UPDATE mod_billing.{prefix}document_lines "
                    "SET document_id=:target,line_number=2 "
                    "WHERE document_id=:source"
                ),
                {"target": target_id, "source": source_id},
            )
        assert error.value.orig.sqlstate == "23001"
        assert (
            connection.scalar(
                text(
                    f"SELECT count(*) FROM mod_billing.{prefix}document_lines "
                    "WHERE document_id=:source"
                ),
                {"source": source_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    f"SELECT count(*) FROM mod_billing.{prefix}document_lines "
                    "WHERE document_id=:target"
                ),
                {"target": target_id},
            )
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_full_credit_exact_replay_precedes_exhausted_receivable_without_new_rows(
    scratch, plane
):
    scope, url = _routing(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    with billing._session(url, scope) as session:
        account, obligation, invoice = billing._seed_invoice(
            session, scope, suffix=str(uuid.uuid4())
        )
        evidence = billing._obligation_command(
            account.id, scope=scope, source_fact_id=obligation.source_fact_id
        )
        command = replace(
            _credit_command(invoice.id),
            pre_tax_amount=billing._money("100"),
            tax_amount=billing._money("20"),
            total_amount=billing._money("120"),
            tax_snapshots=evidence.tax_snapshots,
        )
        credit = issue_credit_note(
            session, scope=scope, command=command, numbering=billing.NUMBERING
        )
        credit_id = credit.id
        session.commit()

        def snapshot():
            # Every Billing evidence/projection row must remain identical.
            tables = (
                models.TENANT_TABLES if plane == "tenant" else models.PLATFORM_TABLES
            )
            return {
                table: session.execute(
                    text(f"SELECT to_jsonb(t) FROM mod_billing.{table} t ORDER BY id")
                )
                .scalars()
                .all()
                for table in tables
            }

        before = snapshot()
        replay = issue_credit_note(
            session, scope=scope, command=command, numbering=billing.NUMBERING
        )
        assert replay.id == credit_id
        session.commit()
        assert snapshot() == before
        with pytest.raises(IdempotencyConflict):
            issue_credit_note(
                session,
                scope=scope,
                command=replace(command, reason="changed request"),
                numbering=billing.NUMBERING,
            )
        with pytest.raises(billing.BillingRuleViolation, match="open receivable"):
            issue_credit_note(
                session,
                scope=scope,
                command=replace(command, correlation_id=f"new:{invoice.id}"),
                numbering=billing.NUMBERING,
            )
        assert snapshot() == before
        assert (
            session.scalar(
                text(
                    f"SELECT count(*) FROM mod_billing.{prefix}documents "
                    "WHERE id=:id AND lifecycle='issued'"
                ),
                {"id": invoice.id},
            )
            == 1
        )


def _mutate_line(
    connection, *, action, plane, scope, document_id, account_id, source_id=None
):
    prefix = "" if plane == "tenant" else "platform_"
    delta = Decimal("0")
    tax_delta = Decimal("0")
    if action == "insert":
        _append_line(
            connection,
            plane=plane,
            scope=scope,
            document_id=document_id,
            account_id=account_id,
        )
        delta = Decimal("20")
    elif action == "update":
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}document_lines "
                "SET description='edited before issue' "
                "WHERE document_id=:id AND line_number=1"
            ),
            {"id": document_id},
        )
    elif action == "delete":
        connection.execute(
            text(
                f"DELETE FROM mod_billing.{prefix}document_lines "
                "WHERE document_id=:id AND line_number=2"
            ),
            {"id": document_id},
        )
        delta = Decimal("-20")
    else:
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}document_lines "
                "SET document_id=:target,line_number=2 "
                "WHERE document_id=:source AND line_number=1"
            ),
            {"target": document_id, "source": source_id},
        )
        delta = Decimal("120")
        tax_delta = Decimal("20")
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents "
                "SET subtotal=0,tax_total=0,grand_total=0 WHERE id=:id"
            ),
            {"id": source_id},
        )
    if delta:
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents "
                "SET subtotal=subtotal+:pre_tax,"
                "tax_total=tax_total+:tax,grand_total=grand_total+:total "
                "WHERE id=:id"
            ),
            {
                "pre_tax": delta - tax_delta,
                "tax": tax_delta,
                "total": delta,
                "id": document_id,
            },
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("winner", ["mutation", "issue"])
@pytest.mark.parametrize("action", ["insert", "update", "delete", "reparent"])
def test_parent_lock_serializes_line_mutation_and_issue_with_matching_snapshot(
    scratch, plane, winner, action
):
    scope, url = _routing(scratch, plane)
    with billing._session(url, scope) as session:
        account_id, document_id = _draft(session, scope, "receipt")
        source_id = None
        if action == "delete":
            _mutate_line(
                session,
                action="insert",
                plane=plane,
                scope=scope,
                document_id=document_id,
                account_id=account_id,
            )
        if action == "reparent":
            _, source_id = _draft(session, scope, "receipt", account_id=account_id)
        session.commit()
    engine = create_engine(scratch[0])
    outcomes = []
    started = threading.Event()

    def mutate(session):
        _mutate_line(
            session,
            action=action,
            plane=plane,
            scope=scope,
            document_id=document_id,
            account_id=account_id,
            source_id=source_id,
        )

    def issue(session):
        issue_document(
            session,
            scope=scope,
            command=billing._issue_command(document_id),
            numbering=billing.NUMBERING,
        )

    def loser():
        try:
            # app_admin exercises the guard rather than online UPDATE/DELETE
            # privilege denial. Issuance still uses the real Billing command.
            with billing._session(scratch[0], scope) as session:
                pid = session.scalar(text("SELECT pg_backend_pid()"))
                outcomes.append(("pid", pid))
                started.set()
                (issue if winner == "mutation" else mutate)(session)
                session.commit()
                outcomes.append(("result", "committed"))
        except DBAPIError as error:
            outcomes.append(("result", error.orig.sqlstate))
        except Exception as error:
            outcomes.append(("unexpected", error))

    with billing._session(scratch[0], scope) as first:
        (mutate if winner == "mutation" else issue)(first)
        thread = threading.Thread(target=loser, daemon=True)
        thread.start()
        try:
            assert started.wait(10), "concurrent writer did not start"
            pid = dict(outcomes)["pid"]
            # Observe an actual PostgreSQL wait before releasing the winner.
            import time

            deadline = time.monotonic() + 10
            with engine.connect() as observer:
                while time.monotonic() < deadline:
                    blockers = observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}
                    )
                    if blockers:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("concurrent statement never waited on the parent lock")
            first.commit()
        finally:
            first.rollback()
            thread.join(15)
    engine.dispose()
    assert not thread.is_alive(), "line/issuance race deadlocked"
    assert "unexpected" not in dict(outcomes), outcomes
    assert dict(outcomes)["result"] == (
        "committed" if winner == "mutation" else "23001"
    ), outcomes
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    line_model = (
        models.DocumentLine if plane == "tenant" else models.PlatformDocumentLine
    )
    expected_count = 1
    expected_total = Decimal("120")
    if action == "delete" and winner == "issue":
        expected_count, expected_total = 2, Decimal("140")
    elif action == "insert" and winner == "mutation":
        expected_count, expected_total = 2, Decimal("140")
    elif action == "reparent" and winner == "mutation":
        expected_count, expected_total = 2, Decimal("240")
    with billing._session(url, scope) as session:
        fact = session.scalars(
            select(fact_model).where(fact_model.document_id == document_id)
        ).one()
        lines = session.scalars(
            select(line_model)
            .where(line_model.document_id == document_id)
            .order_by(line_model.line_number)
        ).all()
        assert len(lines) == len(fact.fact_payload["lines"]) == expected_count
        assert sum(line.total_amount for line in lines) == expected_total
        assert Decimal(fact.fact_payload["grand_total"]["amount"]) == expected_total
        assert [line.description for line in lines] == [
            line["description"] for line in fact.fact_payload["lines"]
        ]
        if action == "update":
            assert lines[0].description == (
                "edited before issue" if winner == "mutation" else "Monthly access"
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_caller_rollback_removes_billing_credit_rows_preserving_original_invoice(
    scratch, plane
):
    """Billing-only rollback proof; NUMBERING is a stateless test adapter."""
    scope, url = _routing(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    with billing._session(url, scope) as session:
        _, _, invoice = billing._seed_invoice(session, scope, suffix=str(uuid.uuid4()))
        credit = issue_credit_note(
            session,
            scope=scope,
            command=_credit_command(invoice.id),
            numbering=billing.NUMBERING,
        )
        credit_id = credit.id
        original_id = invoice.id
        session.rollback()
    with billing._session(url, scope) as session:
        for table, predicate, value in (
            ("documents", "id", credit_id),
            ("document_lines", "document_id", credit_id),
            ("invoice_document_facts", "document_id", credit_id),
            ("posting_groups", "source_ref", f"credit_note:{credit_id}"),
        ):
            assert (
                session.scalar(
                    text(
                        f"SELECT count(*) FROM mod_billing.{prefix}{table} "
                        f"WHERE {predicate}=:value"
                    ),
                    {"value": value},
                )
                == 0
            )
        assert (
            session.scalar(
                text(
                    f"SELECT count(*) FROM mod_billing.{prefix}documents WHERE id=:id"
                ),
                {"id": original_id},
            )
            == 1
        )


def _assert_fresh_issue(session, plane, document_id, account_id, amount, description):
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    effect_model = (
        models.PostingEffect if plane == "tenant" else models.PlatformPostingEffect
    )
    outbox_model = (
        billing.OutboxEvent if plane == "tenant" else billing.PlatformOutboxEvent
    )
    fact = session.scalars(
        select(fact_model).where(fact_model.document_id == document_id)
    ).one()
    payload = fact.fact_payload
    assert Decimal(payload["grand_total"]["amount"]) == amount
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["description"] == description
    assert Decimal(payload["lines"][0]["total_amount"]["amount"]) == amount
    assert (
        session.scalars(
            select(effect_model.amount_delta).where(
                effect_model.billing_account_id == account_id,
                effect_model.lane == "receivable",
            )
        ).one()
        == amount
    )
    emitted = session.scalars(
        select(outbox_model).where(
            outbox_model.event_type == DOCUMENT_FACT_CONTRACT,
            outbox_model.payload["invoice_id"].as_string() == str(document_id),
        )
    ).one()
    assert emitted.payload == {"fact_id": str(fact.id), **payload}


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("autoflush", [False, True])
def test_issue_refreshes_retained_header_and_line_after_independent_commit(
    scratch, plane, autoflush
):
    scope, url = _routing(scratch, plane)
    with billing._session(url, scope) as session:
        account_id, document_id = _draft(session, scope, "receipt")
        session.commit()
    document_model = (
        models.BillingDocument if plane == "tenant" else models.PlatformBillingDocument
    )
    line_model = (
        models.DocumentLine if plane == "tenant" else models.PlatformDocumentLine
    )
    with billing._session(url, scope) as issuer:
        issuer.autoflush = autoflush
        retained_document = issuer.get(document_model, document_id)
        retained_line = issuer.scalars(
            select(line_model).where(line_model.document_id == document_id)
        ).one()
        assert (
            retained_document.grand_total
            == retained_line.total_amount
            == Decimal("120")
        )
        with billing._session(scratch[0], scope) as writer:
            writer.execute(
                document_model.__table__.update()
                .where(document_model.id == document_id)
                .values(
                    subtotal=150,
                    tax_total=0,
                    grand_total=150,
                )
            )
            writer.execute(
                line_model.__table__.update()
                .where(line_model.document_id == document_id)
                .values(
                    description="independently updated",
                    unit_amount=150,
                    pre_tax_amount=150,
                    tax_amount=0,
                    total_amount=150,
                )
            )
            writer.commit()
        assert (
            retained_document.grand_total
            == retained_line.total_amount
            == Decimal("120")
        )
        issue_document(
            issuer,
            scope=scope,
            command=billing._issue_command(document_id),
            numbering=billing.NUMBERING,
        )
        assert issuer.autoflush is autoflush
        assert (
            retained_document.grand_total
            == retained_line.total_amount
            == Decimal("150")
        )
        _assert_fresh_issue(
            issuer,
            plane,
            document_id,
            account_id,
            Decimal("150"),
            "independently updated",
        )
        issuer.commit()
    with billing._session(url, scope) as session:
        _assert_fresh_issue(
            session,
            plane,
            document_id,
            account_id,
            Decimal("150"),
            "independently updated",
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_issue_preserves_pending_owner_edits_and_open_transaction(scratch, plane):
    scope, url = _routing(scratch, plane)
    with billing._session(url, scope) as session:
        account_id, document_id = _draft(session, scope, "receipt")
        session.commit()
    document_model = (
        models.BillingDocument if plane == "tenant" else models.PlatformBillingDocument
    )
    line_model = (
        models.DocumentLine if plane == "tenant" else models.PlatformDocumentLine
    )
    with billing._session(scratch[0], scope) as issuer:
        issuer.autoflush = False
        document = issuer.get(document_model, document_id)
        line = issuer.scalars(
            select(line_model).where(line_model.document_id == document_id)
        ).one()
        document.subtotal = document.grand_total = Decimal("150")
        document.tax_total = Decimal("0")
        line.unit_amount = line.pre_tax_amount = line.total_amount = Decimal("150")
        line.tax_amount = Decimal("0")
        line.description = "pending owner edit"
        root_transaction = issuer.get_transaction()
        completed = []

        def record_completion(session, transaction):
            if transaction.parent is None:
                completed.append(transaction)

        event.listen(issuer, "after_transaction_end", record_completion)
        issue_document(
            issuer,
            scope=scope,
            command=billing._issue_command(document_id),
            numbering=billing.NUMBERING,
        )
        assert issuer.autoflush is False
        assert issuer.get_transaction() is root_transaction
        assert root_transaction.is_active
        assert completed == []
        _assert_fresh_issue(
            issuer, plane, document_id, account_id, Decimal("150"), "pending owner edit"
        )
        issuer.commit()
    with billing._session(url, scope) as session:
        _assert_fresh_issue(
            session,
            plane,
            document_id,
            account_id,
            Decimal("150"),
            "pending owner edit",
        )


def _seed_bi0002_credit(connection, plane, scope):
    """Seed only bi_0002 tables, without current WHT/noncash writer queries."""
    if plane == "tenant":
        account, document, line, group, effect, fact, position = (
            models.BillingAccount,
            models.BillingDocument,
            models.DocumentLine,
            models.PostingGroup,
            models.PostingEffect,
            models.InvoiceDocumentFact,
            models.ReceivablePositionFact,
        )
    else:
        account, document, line, group, effect, fact, position = (
            models.PlatformBillingAccount,
            models.PlatformBillingDocument,
            models.PlatformDocumentLine,
            models.PlatformPostingGroup,
            models.PlatformPostingEffect,
            models.PlatformInvoiceDocumentFact,
            models.PlatformReceivablePositionFact,
        )
    scoped = {"tenant_id": scope.tenant_id} if isinstance(scope, TenantScope) else {}
    account_id, invoice_id, credit_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    connection.execute(
        account.__table__.insert().values(
            id=account_id,
            **scoped,
            external_account_ref=f"bi2:{account_id}",
            currency="NGN",
            minor_units=2,
        )
    )
    presentation = billing._draft_command(uuid.uuid4())
    for version, (document_id, kind, amount, ar, balance) in enumerate(
        (
            (invoice_id, "invoice", "100", "100", "100"),
            (credit_id, "credit_note", "20", "-20", "80"),
        ),
        1,
    ):
        number = f"{kind}:{document_id}"
        values = dict(
            id=document_id,
            **scoped,
            billing_account_id=account_id,
            document_kind=kind,
            credits_document_id=invoice_id if kind == "credit_note" else None,
            lifecycle="draft",
            series_code=kind,
            document_number=None,
            issued_at=None,
            currency="NGN",
            minor_units=2,
            subtotal=Decimal(amount),
            tax_total=Decimal("0"),
            grand_total=Decimal(amount),
            due_date_basis=_jsonable(presentation.due_date_basis),
            document_profile_code=presentation.document_profile_code,
            document_profile_version=presentation.document_profile_version,
            seller_snapshot=_jsonable(presentation.seller_snapshot),
            customer_snapshot=_jsonable(presentation.customer_snapshot),
            payment_instructions=_jsonable(presentation.payment_instructions),
            brand_asset=_jsonable(presentation.brand_asset),
            locale=presentation.locale,
            timezone=presentation.timezone,
        )
        connection.execute(document.__table__.insert().values(**values))
        connection.execute(
            line.__table__.insert().values(
                id=uuid.uuid4(),
                **scoped,
                document_id=document_id,
                billing_account_id=account_id,
                line_number=1,
                description="predecessor service",
                quantity=Decimal("1"),
                unit_code="each",
                unit_amount=Decimal(amount),
                pre_tax_amount=Decimal(amount),
                tax_amount=Decimal("0"),
                total_amount=Decimal(amount),
                currency="NGN",
                minor_units=2,
                price_source_version="historical-price-v1",
            )
        )
        connection.execute(
            document.__table__.update()
            .where(document.id == document_id)
            .values(
                lifecycle="issued",
                document_number=number,
                issued_at=billing.NOW,
            )
        )
        group_id = uuid.uuid4()
        connection.execute(
            group.__table__.insert().values(
                id=group_id,
                **scoped,
                billing_account_id=account_id,
                group_kind="invoice_issued"
                if kind == "invoice"
                else "credit_note_issued",
                currency="NGN",
                minor_units=2,
                source_version=version,
                source_ref=f"document:{document_id}:issue"
                if kind == "invoice"
                else f"credit_note:{document_id}",
                occurred_at=billing.NOW,
            )
        )
        connection.execute(
            effect.__table__.insert().values(
                id=uuid.uuid4(),
                **scoped,
                billing_account_id=account_id,
                posting_group_id=group_id,
                lane="receivable",
                amount_delta=Decimal(ar),
                currency="NGN",
                minor_units=2,
            )
        )
        money = billing._money
        provisional = InvoiceDocumentFactV1(
            scope=scope,
            invoice_id=document_id,
            fact_version=1,
            series_code=kind,
            document_number=number,
            document_kind=kind,
            document_state="issued",
            document_profile_code=presentation.document_profile_code,
            document_profile_version=presentation.document_profile_version,
            currency="NGN",
            minor_units=2,
            subtotal=money(amount),
            tax_total=money("0"),
            grand_total=money(amount),
            due_at=None,
            due_date_basis=presentation.due_date_basis,
            seller_snapshot=presentation.seller_snapshot,
            customer_snapshot=presentation.customer_snapshot,
            payment_instructions=presentation.payment_instructions,
            brand_asset=presentation.brand_asset,
            locale=presentation.locale,
            timezone=presentation.timezone,
            lines=(
                InvoiceLineFactV1(
                    1,
                    "predecessor service",
                    Decimal("1"),
                    "each",
                    money(amount),
                    money(amount),
                    money("0"),
                    money("0"),
                    money(amount),
                    "historical-price-v1",
                ),
            ),
            tax_snapshots=(),
            fx_snapshot=None,
            party_tax_identities=(),
            issued_at=billing.NOW,
            frozen_at=billing.NOW,
            source_authority="internal",
            correlation_id=number,
            presentation_model_digest="",
        )
        payload = _jsonable(provisional)
        payload.pop("presentation_model_digest")
        digest = fingerprint_of(payload)
        payload["presentation_model_digest"] = digest
        connection.execute(
            fact.__table__.insert().values(
                id=uuid.uuid4(),
                **scoped,
                document_id=document_id,
                fact_version=1,
                contract_version=DOCUMENT_FACT_CONTRACT,
                presentation_model_digest=digest,
                fact_payload=payload,
            )
        )
        rebuild = fingerprint_of(
            {
                "collectible_receivable": f"{Decimal(balance):.6f}",
                "available_credit": "0",
                "prepaid_funding": "0",
                "currency": "NGN",
                "minor_units": 2,
            }
        )
        connection.execute(
            position.__table__.insert().values(
                id=uuid.uuid4(),
                **scoped,
                billing_account_id=account_id,
                source_owner="dotmac-billing",
                source_version=version,
                posting_group_watermark=group_id,
                currency="NGN",
                minor_units=2,
                collectible_receivable=Decimal(balance),
                available_credit=Decimal("0"),
                prepaid_funding=Decimal("0"),
                state_fingerprint=rebuild,
                rebuild_fingerprint=rebuild,
                source_authority="internal",
                projection_mode="authoritative",
                derived_from="posting_groups",
                completeness="complete",
                financial_state="open",
                observed_at=billing.NOW,
            )
        )
    return account_id, credit_id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_bi0002_predecessor_upgrade_preserves_evidence_and_closes_credit_append(
    plane,
):
    from alembic import command
    from alembic.config import Config

    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with billing._scratch_database(
        (selection,), target_revision="bi_0002_multiline"
    ) as urls:
        scope, _ = _routing(urls, plane)
        prefix = "" if plane == "tenant" else "platform_"
        engine = create_engine(urls[0])
        with engine.begin() as connection:
            account_id, credit_id = _seed_bi0002_credit(connection, plane, scope)

        def snapshot():
            with engine.connect() as connection:
                return {
                    table: connection.execute(
                        text(
                            f"SELECT to_jsonb(t) FROM mod_billing.{prefix}{table} t "
                            "ORDER BY id"
                        )
                    )
                    .scalars()
                    .all()
                    for table in (
                        "documents",
                        "document_lines",
                        "posting_groups",
                        "posting_effects",
                        "invoice_document_facts",
                        "receivable_position_facts",
                    )
                }

        before = snapshot()
        assert (
            len(before["invoice_document_facts"]) == len(before["posting_effects"]) == 2
        )
        with engine.begin() as connection:
            # Sensitivity proof against the actual predecessor: its guard
            # admits an issued credit-note append. Roll it back immediately
            # so no malformed evidence is carried into the upgrade rehearsal.
            savepoint = connection.begin_nested()
            _append_line(
                connection,
                plane=plane,
                scope=scope,
                document_id=credit_id,
                account_id=account_id,
            )
            savepoint.rollback()
        assert snapshot() == before
        config = Config(str(billing.REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(billing.REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{billing.KERNEL_VERSIONS} {billing.ASSEMBLY_VERSIONS} "
            f"{billing.BILLING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=(selection,)),
        )
        with engine.connect() as writer:
            transaction = writer.begin()
            try:
                # An in-flight predecessor writer must prevent migration
                # completion. The bounded table-lock timeout refuses safely
                # before replacing any guard; retry follows writer rollback.
                _append_line(
                    writer,
                    plane=plane,
                    scope=scope,
                    document_id=credit_id,
                    account_id=account_id,
                )
                with pytest.raises(DBAPIError) as error:
                    command.upgrade(config, "bi_0003_issued_line_freeze")
                assert error.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()
        assert snapshot() == before
        command.upgrade(config, "bi_0003_issued_line_freeze")
        assert snapshot() == before
        with engine.begin() as connection:
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                _append_line(
                    connection,
                    plane=plane,
                    scope=scope,
                    document_id=credit_id,
                    account_id=account_id,
                )
            assert error.value.orig.sqlstate == "23001"
        engine.dispose()
