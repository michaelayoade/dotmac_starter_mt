"""PostgreSQL proofs for the Expenses a2 claim liability lifecycle.

Real migration, real roles: forced RLS isolation on every new table, append-only
evidence, the claim_liabilities column grant and guard trigger, the derived
status CHECK, concurrent settlements serialising on the row lock, and the
downgrade that refuses once anything is populated.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_expenses import (
    AcceptClaimForReimbursement,
    AcceptedClaimLine,
    ApplyDecision,
    ApprovedLineAmount,
    ClaimAccountingReceiptInput,
    ClaimLineDraft,
    CreateCategory,
    CreateClaim,
    Decision,
    ExpensesError,
    HistoricalClaimLiabilityCommand,
    HistoricalClaimLiabilityEvidence,
    HistoricalClaimSettlement,
    PolicyContext,
    RefusalCode,
    accept_claim_for_reimbursement,
    adjust_claim_liability,
    admit_historical_claim_liability,
    apply_claim_decision,
    build_claim_liability_consequence,
    build_claim_settlement_consequence,
    build_claim_settlement_reversal_consequence,
    claim_liability_row_totals,
    create_category,
    create_claim,
    record_claim_accounting_receipt,
    record_claim_settlement,
    reverse_claim_acceptance,
    reverse_claim_settlement,
    submit_claim,
)
from dotmac_expenses.models import (
    TENANT_TABLES,
    ClaimLiability,
    ExpenseClaim,
    metadata_table,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyPerson, PartyType
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
EXPENSES_VERSIONS = (
    ROOT / "packages/dotmac-expenses/src/dotmac_expenses/migrations/versions"
)
NGN = Currency("NGN", 2)
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
A2_TABLES = (
    "claim_liabilities",
    "claim_liability_lines",
    "claim_liability_settlements",
    "claim_settlement_reversals",
    "claim_liability_adjustments",
    "claim_liability_reversals",
    "historical_claim_receipts",
    "claim_accounting_receipts",
)
EVIDENCE_TABLES = A2_TABLES[1:]
LIABILITY = "acct:claims-payable"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Expenses RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"expenses_a2_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command

        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(_alembic_config(), "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _set_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )


def _tenant_session(url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(url))
    _set_tenant(session, tenant_id)
    return session


def _refused(session: Session, tenant_id: uuid.UUID, statement: object, needle: str):
    """Run one statement that must be refused; restore the tenant context a
    rollback discards (a session-level GUC set in a rolled-back transaction)."""
    with pytest.raises(DBAPIError, match=needle):
        session.execute(text(statement) if isinstance(statement, str) else statement)
    session.rollback()
    _set_tenant(session, tenant_id)


def _key() -> dict[str, object]:
    return {"idempotency_key": uuid.uuid4().hex, "idempotency_expires_at": None}


def _seed_party(admin_url: str, tenant_id: uuid.UUID) -> uuid.UUID:
    party_id = uuid.uuid4()
    with Session(create_engine(admin_url)) as session:
        session.add(
            Party(
                id=party_id,
                tenant_id=tenant_id,
                party_type=PartyType.person,
                display_name="Ada Lovelace",
            )
        )
        session.flush()
        session.add(PartyPerson(party_id=party_id, first_name="Ada", last_name="L"))
        session.commit()
    return party_id


def _approved_claim(
    admin_url: str, tenant_id: uuid.UUID, party_id: uuid.UUID, amount: str = "100.00"
) -> uuid.UUID:
    scope = TenantScope(tenant_id)
    with _tenant_session(admin_url, tenant_id) as db:
        category = create_category(
            db,
            scope=scope,
            command=CreateCategory(code=f"c{uuid.uuid4().hex[:6]}", name="Fuel"),
        )
        claim = create_claim(
            db,
            scope=scope,
            command=CreateClaim(
                reference=f"CLM-{uuid.uuid4().hex[:8]}",
                claimant_party_id=party_id,
                purpose="Site visit",
                claim_date=date(2026, 10, 20),
                currency_code="NGN",
                lines=(
                    ClaimLineDraft(
                        category_id=category.id,
                        description="Diesel",
                        claimed_amount=Decimal(amount),
                        expense_date=date(2026, 10, 19),
                    ),
                ),
            ),
            actor_reference="person:ada",
            recorded_at=NOW,
        )
        submit_claim(
            db,
            scope=scope,
            claim_id=claim.id,
            context=PolicyContext(),
            actor_reference="person:ada",
            submitted_at=NOW,
        )
        apply_claim_decision(
            db,
            scope=scope,
            claim_id=claim.id,
            command=ApplyDecision(
                decision=Decision.APPROVED,
                decision_reference=f"approvals:{uuid.uuid4().hex[:6]}",
                approved_lines=(
                    ApprovedLineAmount(
                        line_id=claim.lines[0].id, amount=Decimal(amount)
                    ),
                ),
            ),
            actor_reference="approvals",
            decided_at=NOW,
        )
        claim_id = claim.id
        db.commit()
    return claim_id


def _accept(db: Session, tenant_id: uuid.UUID, claim_id: uuid.UUID) -> ClaimLiability:
    claim = db.get(ExpenseClaim, claim_id)
    return accept_claim_for_reimbursement(
        db,
        scope=TenantScope(tenant_id),
        command=AcceptClaimForReimbursement(
            claim_id=claim_id,
            liability_account_ref=LIABILITY,
            acceptance_reference="ACC-1",
            accepted_by="finance:one",
            lines=(AcceptedClaimLine(claim.lines[0].id, "acct:fuel"),),
        ),
        accepted_at=NOW,
        **_key(),
    )


def _settle(db: Session, tenant_id, liability_id, amount: str, ref: str):
    return record_claim_settlement(
        db,
        scope=TenantScope(tenant_id),
        liability_id=liability_id,
        amount=Money.of(amount, NGN),
        settlement_ref=ref,
        evidence_ref="bank:1",
        value_date=date(2026, 11, 2),
        recorded_by="finance:one",
        recorded_at=NOW + timedelta(minutes=5),
        **_key(),
    )


class _FixedVerifier:
    def __init__(self, evidence: HistoricalClaimLiabilityEvidence) -> None:
        self.evidence = evidence

    def verify(self, *, scope, command, destination):
        return self.evidence


def _historical_evidence(
    tenant_id: uuid.UUID, claim_id: uuid.UUID, party: uuid.UUID
) -> HistoricalClaimLiabilityEvidence:
    return HistoricalClaimLiabilityEvidence(
        tenant_id=tenant_id,
        source_owner="legacy_erp",
        source_claim_id="legacy-claim-1",
        source_snapshot_sha256="a" * 64,
        claim_id=claim_id,
        claimant_party_id=party,
        currency_code="NGN",
        net_payable_amount=Decimal("100.00"),
        settled_before_admission=Decimal("40.00"),
        residual=Decimal("60.00"),
        settlements=(
            HistoricalClaimSettlement("J-1", Decimal("40.00"), "cv1:" + "1" * 64),
        ),
        liability_account_ref=LIABILITY,
        carried_control_amount=Decimal("60.00"),
        carried_account_ref="acct:legacy-2030",
        carried_evidence_ref="gl-extract:1",
        source_journal_ref="JE-100",
        original_posted_at=datetime(2026, 9, 1, tzinfo=UTC),
        accounting_evidence_fingerprint="cv1:" + "2" * 64,
        facts_fingerprint="cv1:" + "3" * 64,
    )


def _alembic_config():
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option(
        "version_locations", f"{KERNEL_VERSIONS} {EXPENSES_VERSIONS}"
    )
    return config


def test_a2_tables_are_declared_and_migrated(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    assert set(A2_TABLES) <= set(TENANT_TABLES)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for table in A2_TABLES:
            forced = conn.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_expenses' AND c.relname = :t"
                ),
                {"t": table},
            ).scalar_one()
            assert forced is True, table
    engine.dispose()


def test_claim_liability_lifecycle_rls_grants_and_guards_on_postgres(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant_a, tenant_b = _seed_tenants(admin_url)
    party = _seed_party(admin_url, tenant_a)
    claim_id = _approved_claim(admin_url, tenant_a, party)
    scope = TenantScope(tenant_a)

    with _tenant_session(app_url, tenant_a) as db:
        liability = _accept(db, tenant_a, claim_id)
        liability_id = liability.id
        accepted = build_claim_liability_consequence(
            db, tenant_id=tenant_a, liability_id=liability_id
        )
        record_claim_accounting_receipt(
            db,
            scope=scope,
            command=ClaimAccountingReceiptInput(
                "expense_claim_liability",
                liability_id,
                accepted.fingerprint,
                "GL-1",
                "e" * 64,
            ),
            recorded_at=NOW,
            **_key(),
        )
        first = _settle(db, tenant_a, liability_id, "60.00", "P1")
        settled = build_claim_settlement_consequence(
            db, tenant_id=tenant_a, settlement_id=first.id, bank_account_ref="acct:bank"
        )
        record_claim_accounting_receipt(
            db,
            scope=scope,
            command=ClaimAccountingReceiptInput(
                "expense_claim_settlement",
                first.id,
                settled.fingerprint,
                "GL-2",
                "e" * 64,
                bank_account_ref="acct:bank",
            ),
            recorded_at=NOW,
            **_key(),
        )
        reversal = reverse_claim_settlement(
            db,
            scope=scope,
            settlement_id=first.id,
            return_ref="RET-1",
            reason="bank returned",
            reversed_by="finance:two",
            reversed_at=NOW + timedelta(hours=1),
            require_distinct_actor=True,
            **_key(),
        )
        assert build_claim_settlement_reversal_consequence(
            db,
            tenant_id=tenant_a,
            reversal_id=reversal.id,
            bank_account_ref="acct:bank",
            posting_date=date(2026, 11, 3),
        )
        _settle(db, tenant_a, liability_id, "40.00", "P2")
        adjust_claim_liability(
            db,
            scope=scope,
            liability_id=liability_id,
            amount=Money.of("-10.00", NGN),
            adjustment_ref="ADJ-1",
            reason="claimant left",
            evidence_ref="memo:1",
            offset_account_ref="acct:unclaimed",
            adjusted_by="finance:one",
            adjusted_at=NOW + timedelta(hours=2),
            **_key(),
        )
        db.commit()
    with _tenant_session(app_url, tenant_a) as db:
        row = db.get(ClaimLiability, liability_id)
        assert (row.amount, row.amount_settled, row.amount_adjusted) == (
            Decimal("100.00"),
            Decimal("40.00"),
            Decimal("-10.00"),
        )
        assert row.status == "partially_settled"
        assert claim_liability_row_totals(
            db, tenant_id=tenant_a, liability_id=liability_id
        ) == (Decimal("40.00"), Decimal("-10.00"))

    # A second claim is accepted then reversed (terminal), and a third legacy
    # claim is admitted with no GL, so every a2 evidence table holds a row.
    second_claim = _approved_claim(admin_url, tenant_a, party)
    third_claim = _approved_claim(admin_url, tenant_a, party)
    with _tenant_session(app_url, tenant_a) as db:
        other = _accept(db, tenant_a, second_claim)
        reverse_claim_acceptance(
            db,
            scope=scope,
            liability_id=other.id,
            reason="in error",
            evidence_ref="memo:2",
            reversed_by="finance:one",
            reversed_at=NOW + timedelta(hours=1),
            **_key(),
        )
        evidence = _historical_evidence(tenant_a, third_claim, party)
        admit_historical_claim_liability(
            db,
            scope=scope,
            command=HistoricalClaimLiabilityCommand(
                source_owner=evidence.source_owner,
                source_claim_id=evidence.source_claim_id,
                claim_id=third_claim,
                source_snapshot_sha256=evidence.source_snapshot_sha256,
                evidence_fingerprint=evidence.fingerprint(),
                evidence_reference="finance:review-7",
                admitted_by="finance:one",
            ),
            verifier=_FixedVerifier(evidence),
            admitted_at=NOW + timedelta(hours=3),
            **_key(),
        )
        db.commit()
        other_id = other.id

    # Forced RLS: another tenant sees nothing and cannot forge a row.
    with _tenant_session(app_url, tenant_b) as db:
        for name in A2_TABLES:
            table = metadata_table(name)
            assert db.scalar(select(func.count()).select_from(table)) == 0, name
        forged = (
            metadata_table("claim_accounting_receipts")
            .insert()
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_a,
                document_kind="k",
                document_id=uuid.uuid4(),
                consequence_fingerprint="a" * 64,
                accounting_reference="r",
                accounting_evidence_fingerprint="b" * 64,
                recorded_at=NOW,
            )
        )
        _refused(db, tenant_b, forged, "row-level security")
    with _tenant_session(app_url, tenant_a) as db:
        for name in A2_TABLES:
            table = metadata_table(name)
            assert db.scalar(select(func.count()).select_from(table)) > 0, name

    # The online role has SELECT/INSERT on evidence and no UPDATE/DELETE.
    with _tenant_session(app_url, tenant_a) as db:
        for name in EVIDENCE_TABLES:
            table = metadata_table(name)
            _refused(
                db,
                tenant_a,
                table.update().values(tenant_id=table.c.tenant_id),
                "permission denied",
            )
            _refused(db, tenant_a, table.delete(), "permission denied")
        # claim_liabilities: UPDATE only on the three projection columns.
        for statement in (
            "UPDATE mod_expenses.claim_liabilities SET amount = amount + 1",
            "UPDATE mod_expenses.claim_liabilities SET liability_account_ref = 'x'",
            "UPDATE mod_expenses.claim_liabilities SET origin = 'historical'",
            "DELETE FROM mod_expenses.claim_liabilities",
        ):
            _refused(db, tenant_a, statement, "permission denied")

    # Even the owner/admin path cannot rewrite evidence or acceptance facts.
    admin = create_engine(admin_url)
    for name in EVIDENCE_TABLES:
        table = metadata_table(name)
        for statement in (
            table.update().values(tenant_id=table.c.tenant_id),
            table.delete(),
        ):
            with pytest.raises(DBAPIError, match="append-only"), admin.begin() as conn:
                conn.execute(statement)
    for statement, needle in (
        (
            "UPDATE mod_expenses.claim_liabilities SET amount = amount + 1",
            "immutable",
        ),
        (
            "UPDATE mod_expenses.claim_liabilities SET claimant_party_id = "
            "gen_random_uuid()",
            "immutable|foreign key",
        ),
        (
            "UPDATE mod_expenses.claim_liabilities SET liability_account_ref = 'x'",
            "immutable",
        ),
        ("DELETE FROM mod_expenses.claim_liabilities", "permanent financial"),
        # Reversal needs OPEN and its reversal row (the first liability is not).
        (
            "UPDATE mod_expenses.claim_liabilities SET status = 'reversed', "
            "amount_settled = 0, amount_adjusted = 0 "
            "WHERE status = 'partially_settled'",
            "reversed only from open",
        ),
        # The derived-status CHECK holds at the database.
        (
            "UPDATE mod_expenses.claim_liabilities SET status = 'settled' "
            "WHERE status = 'partially_settled'",
            "ck_claim_liabilities_derived",
        ),
        (
            "UPDATE mod_expenses.claim_liabilities SET amount_settled = 91.00, "
            "status = 'settled' WHERE status = 'partially_settled'",
            "ck_claim_liabilities_amounts",
        ),
    ):
        with pytest.raises(DBAPIError, match=needle), admin.begin() as conn:
            conn.execute(text(statement))

    # REVERSED is terminal at the database.
    with pytest.raises(DBAPIError, match="terminal"), admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_expenses.claim_liabilities SET status = 'open' "
                "WHERE id = :id"
            ),
            {"id": other_id},
        )
    admin.dispose()


def test_concurrent_settlements_serialise_on_the_liability_row(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant_a, _ = _seed_tenants(admin_url)
    party = _seed_party(admin_url, tenant_a)
    claim_id = _approved_claim(admin_url, tenant_a, party)
    with _tenant_session(app_url, tenant_a) as db:
        liability_id = _accept(db, tenant_a, claim_id).id
        db.commit()

    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def settle(ref: str) -> None:
        with _tenant_session(app_url, tenant_a) as db:
            barrier.wait()
            try:
                _settle(db, tenant_a, liability_id, "60.00", ref)
                db.commit()
                outcomes.append("ok")
            except ExpensesError as exc:
                db.rollback()
                outcomes.append(exc.code)

    threads = [threading.Thread(target=settle, args=(f"P{i}",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(map(str, outcomes)) == [
        "ok",
        RefusalCode.SETTLEMENT_EXCEEDS_OUTSTANDING.value,
    ]
    with _tenant_session(app_url, tenant_a) as db:
        row = db.get(ClaimLiability, liability_id)
        assert row.amount_settled == Decimal("60.00")
        assert row.status == "partially_settled"
        assert claim_liability_row_totals(
            db, tenant_id=tenant_a, liability_id=liability_id
        ) == (Decimal("60.00"), Decimal(0))


def test_downgrade_refused_when_populated(
    migrated_scratch: tuple[str, str],
) -> None:
    from alembic import command

    admin_url, app_url = migrated_scratch
    tenant_a, _ = _seed_tenants(admin_url)
    party = _seed_party(admin_url, tenant_a)
    claim_id = _approved_claim(admin_url, tenant_a, party)
    with _tenant_session(app_url, tenant_a) as db:
        _accept(db, tenant_a, claim_id)
        db.commit()
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    with pytest.raises(RuntimeError, match="require forward correction"):
        command.downgrade(_alembic_config(), "ex_0001_expenses")
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT to_regclass('mod_expenses.claim_liabilities')")
            ).scalar_one()
            is not None
        )
    admin.dispose()


def test_downgrade_succeeds_when_empty_and_upgrade_returns(
    migrated_scratch: tuple[str, str],
) -> None:
    from alembic import command

    admin_url, _ = migrated_scratch
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    config = _alembic_config()
    command.downgrade(config, "ex_0001_expenses")
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        for table in A2_TABLES:
            assert (
                conn.execute(
                    text("SELECT to_regclass(:name)"),
                    {"name": f"mod_expenses.{table}"},
                ).scalar_one()
                is None
            ), table
        # a1 is intact.
        assert (
            conn.execute(
                text("SELECT to_regclass('mod_expenses.expense_claims')")
            ).scalar_one()
            is not None
        )
    admin.dispose()
    command.upgrade(config, "heads")
