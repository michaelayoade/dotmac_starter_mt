"""Source-owner canary: policy changes never rewrite sealed financial facts."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_kernel.money import Money
from dotmac_tax import (
    TaxConflict,
    TaxFact,
    TaxNotFound,
    TaxRuleInput,
    TaxRuleSupersessionInput,
    TaxRuleViolation,
    determine_tax_set,
    preview_tax_policy,
    publish_tax_rule,
    supersede_tax_rule,
)
from dotmac_tax.models import TaxDeterminationSet, TaxRuleSupersession
from sqlalchemy import func, select, text

from tests import test_tax_released_migration_upgrade as upgrade_helpers
from tests.unit import test_tax_service as helpers

NGN = helpers.NGN
NOW = helpers.NOW
_masters = helpers._masters
_tenant = helpers._tenant
db = helpers.db


def _setup(db, *, effective_to=None, historical=False):
    tenant = _tenant(db)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant.id)},
        )
    _, jurisdiction, code = _masters(db, tenant.id)
    command = TaxRuleInput(
        code.id,
        1,
        date(2026, 1, 1),
        effective_to,
        10,
        "invoice",
        "issued",
        "output",
        "percentage",
        Decimal("0.1"),
        None,
        False,
        Decimal(0),
    )
    if historical:
        rule = SimpleNamespace(id=uuid4())
        db.execute(
            text("""
            INSERT INTO mod_tax.tax_rules
                (id, tenant_id, tax_code_id, version, effective_from, effective_to,
                 priority, fact_kind, recognition_basis_code, transaction_side,
                 calculation_method, rate, inclusive, recoverable_rate,
                 treatment_code, calculation_sequence, calculation_base_code,
                 published_at)
            VALUES (:id, :tenant, :code, 1, '2026-01-01', :end, 10, 'invoice',
                'issued', 'output', 'percentage', 0.1, false, 0, 'standard_rated',
                100, 'source_amount', :published_at)
        """),
            {
                "id": rule.id,
                "tenant": tenant.id,
                "code": code.id,
                "end": effective_to,
                "published_at": NOW,
            },
        )
    else:
        rule = publish_tax_rule(db, tenant_id=tenant.id, command=command)
    fact = TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=date(2026, 6, 1),
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side="output",
        base_amount=Money.of("100", NGN),
        source_ref="invoice:A",
        source_version="1",
        evidence_ref="invoice:A",
    )
    successor = TaxRuleSupersessionInput(
        rule.id,
        1,
        replace(
            command, version=2, effective_from=date(2026, 7, 1), rate=Decimal("0.2")
        ),
        date(2026, 7, 1),
        uuid4(),
        "policy-editor",
        "approved adjustment",
        "approval:1",
        "policy:1",
        "1",
    )
    return tenant.id, rule, fact, successor


def test_cross_tenant_predecessor_is_not_found(db):
    tenant_id, _, _, command = _setup(db)
    other = _tenant(db)
    with pytest.raises(TaxNotFound):
        supersede_tax_rule(db, tenant_id=other.id, command=command)
    assert db.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 0


def test_effective_boundary_and_sealed_replay(db):
    tenant_id, predecessor, fact, command = _setup(db)
    original = determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
    edge = supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    assert supersede_tax_rule(db, tenant_id=tenant_id, command=command) == edge
    assert predecessor.effective_to is None
    for offset, amount in [(-1, "10"), (0, "20"), (1, "20")]:
        changed = replace(
            fact,
            occurred_on=command.effective_from + timedelta(days=offset),
            source_ref=f"invoice:{offset}",
        )
        result = determine_tax_set(
            db, tenant_id=tenant_id, fact=changed, determined_at=NOW
        )
        assert result.tax_amount == Money.of(amount, NGN)
    assert (
        determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
        == original
    )
    with pytest.raises(TaxConflict):
        supersede_tax_rule(
            db, tenant_id=tenant_id, command=replace(command, reason="different")
        )
    with pytest.raises(TaxConflict):
        supersede_tax_rule(
            db, tenant_id=tenant_id, command=replace(command, source_ref="other")
        )


def test_preview_uses_same_policy_without_persisting(db, monkeypatch):
    tenant_id, _, fact, command = _setup(db)
    supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    fact = replace(fact, occurred_on=command.effective_from)
    pending_before = set(db.new)
    with monkeypatch.context() as guard:

        def forbidden_flush(*args, **kwargs):
            raise AssertionError("preview attempted a flush")

        guard.setattr(db, "flush", forbidden_flush)
        preview = preview_tax_policy(
            db, tenant_id=tenant_id, fact=fact, determined_at=NOW
        )
    assert set(db.new) == pending_before
    assert not hasattr(preview, "determination_set_id")
    assert not hasattr(preview, "result_fingerprint")
    assert preview.tax_amount == Money.of("20", NGN)
    assert db.scalar(select(func.count()).select_from(TaxDeterminationSet)) == 0
    sealed = determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
    assert preview.tax_amount == sealed.tax_amount
    assert preview.components[0].rule_id == sealed.components[0].rule_id


def test_unrelated_tie_still_refuses_and_invalid_scope_does_not_publish(db):
    tenant_id, _, fact, command = _setup(db)
    with pytest.raises(TaxRuleViolation):
        supersede_tax_rule(
            db,
            tenant_id=tenant_id,
            command=replace(
                command, replacement=replace(command.replacement, priority=11)
            ),
        )
    supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    publish_tax_rule(
        db, tenant_id=tenant_id, command=replace(command.replacement, version=3)
    )
    with pytest.raises(TaxRuleViolation, match="ambiguous"):
        determine_tax_set(
            db,
            tenant_id=tenant_id,
            fact=replace(fact, occurred_on=command.effective_from),
            determined_at=NOW,
        )


def test_chained_replacement_has_no_revival_and_owner_can_rollback(db, monkeypatch):
    tenant_id, _, fact, command = _setup(db)
    db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("module tried to own transaction")

    with monkeypatch.context() as guard:
        guard.setattr(db, "begin_nested", forbidden)
        guard.setattr(db, "commit", forbidden)
        guard.setattr(db, "rollback", forbidden)
        edge = supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    next_command = replace(
        command,
        predecessor_rule_id=edge.replacement.rule_id,
        predecessor_version=2,
        effective_from=date(2026, 8, 1),
        replacement=replace(
            command.replacement,
            version=3,
            effective_from=date(2026, 8, 1),
            rate=Decimal("0.3"),
        ),
        source_ref="policy:2",
    )
    supersede_tax_rule(db, tenant_id=tenant_id, command=next_command)
    for day, amount in [(date(2026, 7, 15), "20"), (date(2026, 8, 1), "30")]:
        result = preview_tax_policy(
            db,
            tenant_id=tenant_id,
            fact=replace(fact, occurred_on=day),
            determined_at=NOW,
        )
        assert result.tax_amount == Money.of(amount, NGN)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 0
    result = preview_tax_policy(
        db,
        tenant_id=tenant_id,
        fact=replace(fact, occurred_on=date(2026, 8, 1)),
        determined_at=NOW,
    )
    assert result.tax_amount == Money.of("10", NGN)


def test_progressive_and_compound_basis_change_share_preview_and_determination(db):
    from dotmac_tax import TaxRuleBandInput, create_tax_code

    tenant_id, _, fact, command = _setup(db)
    code = create_tax_code(
        db,
        tenant_id=tenant_id,
        jurisdiction_id=fact.jurisdiction_id,
        code="LEVY",
        name="Compound levy",
        tax_kind_code="custom",
    )
    publish_tax_rule(
        db,
        tenant_id=tenant_id,
        command=replace(
            command.replacement,
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            calculation_sequence=200,
            rate=Decimal("0.1"),
        ),
    )
    successor = replace(
        command,
        replacement=replace(
            command.replacement,
            calculation_method="progressive",
            rate=None,
            bands=(
                TaxRuleBandInput(1, Decimal(0), Decimal(50), Decimal("0.1")),
                TaxRuleBandInput(2, Decimal(50), None, Decimal("0.3")),
            ),
        ),
    )
    supersede_tax_rule(db, tenant_id=tenant_id, command=successor)
    # The second policy's explicit basis adjustment compounds on the first tax.
    levy = db.scalar(
        select(helpers.TaxRule).where(helpers.TaxRule.tax_code_id == code.id)
    )
    supersede_tax_rule(
        db,
        tenant_id=tenant_id,
        command=replace(
            command,
            predecessor_rule_id=levy.id,
            source_ref="policy:levy",
            replacement=replace(
                command.replacement,
                tax_code_id=code.id,
                calculation_sequence=200,
                calculation_base_code="source_plus_prior_tax",
                rate=Decimal("0.1"),
            ),
        ),
    )
    fact = replace(fact, occurred_on=command.effective_from)
    preview = preview_tax_policy(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
    result = determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
    assert preview.tax_amount == result.tax_amount == Money.of("32", NGN)
    assert preview.components[1].base_amount == Money.of("120", NGN)
    assert len(preview.components[0].lines) == 2


# The fixtures below execute only in Git-hosted CI with PostgreSQL configured.

database = upgrade_helpers.database


@pytest.fixture
def upgraded_policy_database(database, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from alembic import command as alembic_command

    super_url, admin_url, app_url = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = upgrade_helpers._config(upgrade_helpers.CURRENT_TAX)
    # Real predecessor lineage: author immutable policy under tx_0003 first.
    alembic_command.upgrade(config, "tx_0003_result_fingerprint")
    engine = create_engine(admin_url)
    with Session(engine) as session:
        tenant_id, rule, fact, supersession = _setup(session, historical=True)
        rule_id = rule.id
        session.commit()
    alembic_command.upgrade(config, "tax@head")
    with Session(engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        original = determine_tax_set(
            session, tenant_id=tenant_id, fact=fact, determined_at=NOW
        )
        session.commit()
    try:
        yield (
            tenant_id,
            rule_id,
            fact,
            supersession,
            original,
            config,
            admin_url,
            app_url,
        )
    finally:
        engine.dispose()


def _online_session(url, tenant_id, *, isolation="READ COMMITTED"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(url, isolation_level=isolation)
    session = Session(engine)
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    return session, engine


def test_postgres_upgrade_role_rls_append_only_and_downgrade_refusal(
    upgraded_policy_database,
):
    from sqlalchemy.exc import DBAPIError

    from alembic import command as alembic_command

    tenant_id, rule_id, fact, command, original, config, admin_url, app_url = (
        upgraded_policy_database
    )
    session, engine = _online_session(app_url, tenant_id)
    try:
        edge = supersede_tax_rule(session, tenant_id=tenant_id, command=command)
        assert (
            determine_tax_set(
                session, tenant_id=tenant_id, fact=fact, determined_at=NOW
            )
            == original
        )
        session.commit()
        # Every prohibited write is tried in its own transaction; the owner resets RLS.
        for sql in [
            "UPDATE mod_tax.tax_rule_supersessions SET reason = 'changed'",
            "DELETE FROM mod_tax.tax_rule_supersessions",
            "UPDATE mod_tax.tax_rules SET rate = 0.9",
            "UPDATE mod_tax.tax_determination_sets SET tax_amount = 99",
            "UPDATE mod_tax.tax_determinations SET tax_amount = 99",
            "UPDATE mod_tax.tax_determination_lines SET tax_amount = 99",
        ]:
            session.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            with pytest.raises(DBAPIError):
                session.execute(text(sql))
            session.rollback()
        other = uuid4()
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(other)},
        )
        assert (
            session.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 0
        )
        with pytest.raises(TaxNotFound):
            supersede_tax_rule(session, tenant_id=other, command=command)
        session.rollback()
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        replay = supersede_tax_rule(session, tenant_id=tenant_id, command=command)
        assert replay == edge
        session.rollback()
        with pytest.raises(DBAPIError, match="cannot downgrade"):
            alembic_command.downgrade(config, "tx_0003_result_fingerprint")
    finally:
        session.close()
        engine.dispose()


def test_postgres_competing_authors_have_one_winner(upgraded_policy_database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    tenant_id, _, _, command, _, _, _, app_url = upgraded_policy_database
    first, first_engine = _online_session(app_url, tenant_id)
    second_attempt = Event()
    contender = replace(command, source_ref="competing-policy")

    def compete():
        session, engine = _online_session(app_url, tenant_id)

        @event.listens_for(engine, "before_cursor_execute")
        def observe_lock(connection, cursor, statement, parameters, context, many):
            if "FOR NO KEY UPDATE" in statement:
                second_attempt.set()

        try:
            with pytest.raises(TaxConflict, match="already been superseded"):
                supersede_tax_rule(session, tenant_id=tenant_id, command=contender)
            session.rollback()
        finally:
            session.close()
            engine.dispose()

    try:
        edge = supersede_tax_rule(first, tenant_id=tenant_id, command=command)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(compete)
            try:
                assert second_attempt.wait(
                    15
                ), "contending author never reached code lock"
            finally:
                first.commit()
            future.result(timeout=20)
        first.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        assert first.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 1
        assert supersede_tax_rule(first, tenant_id=tenant_id, command=command) == edge
    finally:
        first.close()
        first_engine.dispose()


def test_postgres_selector_keeps_one_statement_snapshot_during_author_commit(
    upgraded_policy_database,
):
    from sqlalchemy import event

    tenant_id, _, fact, command, _, _, _, app_url = upgraded_policy_database
    author, author_engine = _online_session(app_url, tenant_id)
    reader, reader_engine = _online_session(app_url, tenant_id)
    supersede_tax_rule(author, tenant_id=tenant_id, command=command)
    committed = []

    @event.listens_for(reader_engine, "after_cursor_execute")
    def commit_between_selection_and_projection(
        connection, cursor, statement, parameters, context, many
    ):
        if (
            "FROM mod_tax.tax_rules" in statement
            and "JOIN mod_tax.tax_codes" in statement
            and not committed
        ):
            # Commit after the candidate statement's snapshot, before projection.
            author.commit()
            committed.append(True)

    try:
        dated = replace(
            fact, occurred_on=command.effective_from, source_ref="snapshot-race"
        )
        before = determine_tax_set(
            reader, tenant_id=tenant_id, fact=dated, determined_at=NOW
        )
        assert committed == [True]
        assert before.tax_amount == Money.of("10", NGN)
        reader.commit()
        reader.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        after = determine_tax_set(
            reader,
            tenant_id=tenant_id,
            fact=replace(dated, source_ref="snapshot-after"),
            determined_at=NOW,
        )
        assert after.tax_amount == Money.of("20", NGN)
    finally:
        reader.close()
        reader_engine.dispose()
        author.close()
        author_engine.dispose()


def test_postgres_downgrade_waits_for_concurrent_evidence_before_deciding(
    upgraded_policy_database,
):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    from sqlalchemy.exc import DBAPIError

    from alembic import command as alembic_command

    tenant_id, _, _, command, _, config, _, app_url = upgraded_policy_database
    author, author_engine = _online_session(app_url, tenant_id)
    observer, observer_engine = _online_session(app_url, tenant_id)

    def downgrade():
        with pytest.raises(DBAPIError, match="cannot downgrade"):
            alembic_command.downgrade(config, "tx_0003_result_fingerprint")

    try:
        supersede_tax_rule(author, tenant_id=tenant_id, command=command)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(downgrade)
            try:
                deadline = monotonic() + 3
                waiting = False
                while monotonic() < deadline:
                    waiting = bool(
                        observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks "
                                "WHERE relation IN ('mod_tax.tax_rules'::regclass, "
                                "'mod_tax.tax_rule_supersessions'::regclass) "
                                "AND mode = 'AccessExclusiveLock' AND NOT granted)"
                            )
                        )
                    )
                    if waiting:
                        break
                    sleep(0.025)
                assert (
                    waiting
                ), "downgrade never acquired admission lock before evidence check"
            finally:
                author.commit()
            future.result(timeout=10)
        observer.rollback()
        observer.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        assert (
            observer.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 1
        )
    finally:
        author.close()
        author_engine.dispose()
        observer.close()
        observer_engine.dispose()


def test_scheduled_policy_correction_at_same_boundary_keeps_sealed_replay(db):
    tenant_id, _, fact, command = _setup(db)
    edge = supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    fact = replace(fact, occurred_on=command.effective_from)
    original = determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
    correction = replace(
        command,
        predecessor_rule_id=edge.replacement.rule_id,
        predecessor_version=2,
        source_ref="correction",
        replacement=replace(command.replacement, version=3, rate=Decimal("0.3")),
    )
    corrected = supersede_tax_rule(db, tenant_id=tenant_id, command=correction)
    assert supersede_tax_rule(db, tenant_id=tenant_id, command=correction) == corrected
    assert (
        determine_tax_set(db, tenant_id=tenant_id, fact=fact, determined_at=NOW)
        == original
    )
    new = determine_tax_set(
        db,
        tenant_id=tenant_id,
        fact=replace(fact, source_ref="corrected-new-fact"),
        determined_at=NOW,
    )
    assert new.tax_amount == Money.of("30", NGN)


def test_postgres_hidden_evidence_makes_downgrade_fail_closed(upgraded_policy_database):
    from importlib import import_module

    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    tenant_id, _, _, command, _, _, admin_url, app_url = upgraded_policy_database
    author, author_engine = _online_session(app_url, tenant_id)
    admin_engine = create_engine(admin_url)
    try:
        supersede_tax_rule(author, tenant_id=tenant_id, command=command)
        author.commit()
        # Give a non-bypass role admission-lock rights in this disposable DB.
        # It still cannot see other tenants or safely make a global drop decision.
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "GRANT UPDATE, DELETE ON mod_tax.tax_rules, "
                    "mod_tax.tax_rule_bands, mod_tax.tax_rule_supersessions TO app_user"
                )
            )
        hidden, hidden_engine = _online_session(app_url, uuid4())
        try:
            assert (
                hidden.scalar(select(func.count()).select_from(TaxRuleSupersession))
                == 0
            )
            migration = import_module(
                "dotmac_tax.migrations.versions.tx_0004_rule_supersession"
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                with Operations.context(
                    MigrationContext.configure(hidden.connection())
                ):
                    migration.downgrade()
            hidden.rollback()
        finally:
            hidden.close()
            hidden_engine.dispose()
        author.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        assert author.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 1
    finally:
        author.close()
        author_engine.dispose()
        admin_engine.dispose()


@pytest.mark.parametrize(
    "change",
    [
        {"effective_from": date(2025, 12, 31)},
        {"effective_to": date(2026, 12, 31)},
        {"version": 1},
    ],
)
def test_invalid_replacement_interval_or_version_refuses_before_publication(db, change):
    tenant_id, _, _, command = _setup(db)
    command = replace(command, replacement=replace(command.replacement, **change))
    if "effective_from" in change:
        command = replace(command, effective_from=change["effective_from"])
    with pytest.raises(TaxRuleViolation):
        supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    assert db.scalar(select(func.count()).select_from(TaxRuleSupersession)) == 0
    assert db.scalar(select(func.count()).select_from(helpers.TaxRule)) == 1


def test_replacement_inherits_finite_end_and_cannot_revive_predecessor(db):
    tenant_id, _, fact, command = _setup(db, effective_to=date(2026, 7, 31))
    supersede_tax_rule(db, tenant_id=tenant_id, command=command)
    last = preview_tax_policy(
        db,
        tenant_id=tenant_id,
        fact=replace(fact, occurred_on=date(2026, 7, 31)),
        determined_at=NOW,
    )
    assert last.tax_amount == Money.of("20", NGN)
    with pytest.raises(TaxRuleViolation, match="no applicable"):
        preview_tax_policy(
            db,
            tenant_id=tenant_id,
            fact=replace(fact, occurred_on=date(2026, 8, 1)),
            determined_at=NOW,
        )


def _insert_unpublished_rule(session, tenant_id, predecessor_id, *, published=False):
    rule_id = uuid4()
    session.execute(
        text("""
        INSERT INTO mod_tax.tax_rules
            (id, tenant_id, tax_code_id, version, effective_from, effective_to,
             priority, fact_kind, recognition_basis_code, transaction_side,
             calculation_method, rate, fixed_amount, inclusive, recoverable_rate,
             party_category, supply_category, place_code, treatment_code,
             calculation_sequence, calculation_base_code, published_at,
             is_published)
        SELECT :id, tenant_id, tax_code_id, 9000, effective_from, effective_to,
             priority, fact_kind, recognition_basis_code, transaction_side,
             'progressive', NULL, NULL, false, recoverable_rate,
             party_category, supply_category, place_code, treatment_code,
             calculation_sequence, calculation_base_code, published_at, :published
        FROM mod_tax.tax_rules WHERE tenant_id = :tenant AND id = :predecessor
    """),
        {
            "id": rule_id,
            "tenant": tenant_id,
            "predecessor": predecessor_id,
            "published": published,
        },
    )
    return rule_id


def _insert_band(session, tenant_id, rule_id, *, sequence=1):
    session.execute(
        text("""
        INSERT INTO mod_tax.tax_rule_bands
            (id, tenant_id, rule_id, sequence, lower_bound, upper_bound, rate)
        VALUES (:id, :tenant, :rule, :sequence, 0, NULL, 0.1)
    """),
        {"id": uuid4(), "tenant": tenant_id, "rule": rule_id, "sequence": sequence},
    )


@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_postgres_rule_band_membership_seals_in_creating_transaction(
    upgraded_policy_database, isolation
):
    from dotmac_tax import get_tax_rule
    from sqlalchemy.exc import DBAPIError

    tenant_id, predecessor_id, fact, _, _, _, _, app_url = upgraded_policy_database
    session, engine = _online_session(app_url, tenant_id, isolation=isolation)
    try:
        rule_id = _insert_unpublished_rule(session, tenant_id, predecessor_id)
        _insert_band(session, tenant_id, rule_id)
        with pytest.raises(TaxNotFound):
            get_tax_rule(
                session,
                tenant_id=tenant_id,
                tax_code_id=session.scalar(
                    select(helpers.TaxRule.tax_code_id).where(
                        helpers.TaxRule.id == predecessor_id
                    )
                ),
                version=9000,
            )
        # The transient, unpublished equal-priority candidate never enters selection.
        assert preview_tax_policy(
            session, tenant_id=tenant_id, fact=fact, determined_at=NOW
        ).tax_amount == Money.of("10", NGN)
        session.execute(
            text(
                "UPDATE mod_tax.tax_rules SET is_published = true "
                "WHERE tenant_id = :tenant AND id = :rule"
            ),
            {"tenant": tenant_id, "rule": rule_id},
        )
        with pytest.raises(DBAPIError, match="band membership is published"):
            _insert_band(session, tenant_id, rule_id, sequence=2)
        session.rollback()
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        assert session.scalar(select(func.count()).select_from(helpers.TaxRule)) == 1
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_postgres_unpublished_parent_cannot_escape_transaction(
    upgraded_policy_database, isolation
):
    from sqlalchemy.exc import DBAPIError

    tenant_id, predecessor_id, _, _, _, _, _, app_url = upgraded_policy_database
    session, engine = _online_session(app_url, tenant_id, isolation=isolation)
    try:
        rule_id = _insert_unpublished_rule(session, tenant_id, predecessor_id)
        _insert_band(session, tenant_id, rule_id)
        with pytest.raises(DBAPIError, match="must be published before commit"):
            session.commit()
        session.rollback()
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        assert session.scalar(select(func.count()).select_from(helpers.TaxRule)) == 1
        with pytest.raises(DBAPIError, match="publication is immutable"):
            _insert_unpublished_rule(session, tenant_id, predecessor_id, published=True)
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_postgres_old_published_rule_refuses_late_band_insert(upgraded_policy_database):
    from sqlalchemy.exc import DBAPIError

    tenant_id, predecessor_id, _, _, _, _, _, app_url = upgraded_policy_database
    session, engine = _online_session(app_url, tenant_id)
    try:
        assert (
            session.scalar(
                select(helpers.TaxRule.is_published).where(
                    helpers.TaxRule.id == predecessor_id
                )
            )
            is True
        )
        with pytest.raises(DBAPIError, match="band membership is published"):
            _insert_band(session, tenant_id, predecessor_id)
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_postgres_public_progressive_replacement_refuses_band_mutation(
    upgraded_policy_database,
):
    from dotmac_tax import TaxRuleBandInput
    from sqlalchemy.exc import DBAPIError

    tenant_id, _, fact, command, _, _, _, app_url = upgraded_policy_database
    session, engine = _online_session(app_url, tenant_id)
    try:
        command = replace(
            command,
            replacement=replace(
                command.replacement,
                calculation_method="progressive",
                rate=None,
                bands=(TaxRuleBandInput(1, Decimal(0), None, Decimal("0.2")),),
            ),
        )
        edge = supersede_tax_rule(session, tenant_id=tenant_id, command=command)
        result = determine_tax_set(
            session,
            tenant_id=tenant_id,
            fact=replace(fact, occurred_on=command.effective_from),
            determined_at=NOW,
        )
        assert result.tax_amount == Money.of("20", NGN)
        session.commit()
        for sql in (
            "UPDATE mod_tax.tax_rule_bands SET rate = 0.9 WHERE rule_id = :rule",
            "DELETE FROM mod_tax.tax_rule_bands WHERE rule_id = :rule",
            "UPDATE mod_tax.tax_rules SET is_published = false WHERE id = :rule",
            "UPDATE mod_tax.tax_rules SET is_published = true WHERE id = :rule",
        ):
            session.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            with pytest.raises(DBAPIError):
                session.execute(text(sql), {"rule": edge.replacement.rule_id})
            session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_postgres_empty_downgrade_restores_predecessor_schema_and_trigger(
    database, monkeypatch
):
    from sqlalchemy import create_engine

    from alembic import command as alembic_command

    _, admin_url, _ = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = upgrade_helpers._config(upgrade_helpers.CURRENT_TAX)
    alembic_command.upgrade(config, "heads")
    alembic_command.downgrade(config, "tx_0003_result_fingerprint")
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'mod_tax' AND table_name = 'tax_rules'
                  AND column_name = 'is_published'
            """)
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("""
                SELECT tgtype FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'mod_tax.tax_rules'::regclass
                  AND tgname = 'protect_tax_evidence'
            """)
                )
                == 27
            )  # ROW + BEFORE + UPDATE + DELETE; no INSERT interception.
    finally:
        engine.dispose()


def _insert_edge_sql(session, tenant_id, command, candidate_rule_id, **changes):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "predecessor_rule_id": command.predecessor_rule_id,
        "predecessor_version": command.predecessor_version,
        "replacement_rule_id": candidate_rule_id,
        "replacement_version": command.replacement.version,
        "effective_from": command.effective_from,
        "actor_id": command.actor_id,
        "published_by_ref": command.published_by_ref,
        "reason": command.reason,
        "evidence_ref": command.evidence_ref,
        "source_ref": command.source_ref,
        "source_version": command.source_version,
        "published_at": NOW,
    }
    values.update(changes)
    session.execute(
        text("""
        INSERT INTO mod_tax.tax_rule_supersessions
            (id, tenant_id, predecessor_rule_id, predecessor_version,
             replacement_rule_id, replacement_version, effective_from,
             actor_id, published_by_ref, reason, evidence_ref,
             source_ref, source_version, published_at)
        VALUES (:id, :tenant_id, :predecessor_rule_id, :predecessor_version,
            :replacement_rule_id, :replacement_version, :effective_from,
            :actor_id, :published_by_ref, :reason, :evidence_ref,
            :source_ref, :source_version, :published_at)
    """),
        values,
    )


def _reset_tenant(session, tenant_id):
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )


@pytest.mark.parametrize("role", ["owner", "online"])
def test_postgres_direct_edge_invariants_and_owner_immutability(
    upgraded_policy_database, role
):
    from dotmac_tax import create_tax_code
    from sqlalchemy.exc import DBAPIError

    tenant_id, predecessor_id, fact, command, original, _, admin_url, app_url = (
        upgraded_policy_database
    )
    setup, setup_engine = _online_session(admin_url, tenant_id)
    candidates = []
    try:
        other_code = create_tax_code(
            setup,
            tenant_id=tenant_id,
            jurisdiction_id=fact.jurisdiction_id,
            code="OTHER",
            name="Other levy",
            tax_kind_code="custom",
        )
        invalid_contents = [
            {"tax_code_id": other_code.id},
            {"priority": 11},
            {"fact_kind": "other-fact"},
            {"recognition_basis_code": "other-basis"},
            {"transaction_side": "input"},
            {"party_category": "different-party"},
            {"supply_category": "different-supply"},
            {"place_code": "different-place"},
            {"effective_from": date(2025, 12, 31)},
            {"effective_to": date(2026, 12, 31)},
        ]
        for version, changes in enumerate(invalid_contents, 100):
            candidate_command = replace(command.replacement, version=version, **changes)
            candidate = publish_tax_rule(
                setup, tenant_id=tenant_id, command=candidate_command
            )
            candidates.append(
                (
                    candidate.id,
                    candidate.version,
                    {"effective_from": candidate_command.effective_from},
                )
            )
        positive = publish_tax_rule(
            setup, tenant_id=tenant_id, command=command.replacement
        )
        positive_id = positive.id
        # Proper content lets each edge-metadata refusal isolate the database guard.
        for changes in [
            {"predecessor_version": 99},
            {"replacement_version": 99},
            {"effective_from": date(2026, 7, 2)},
            {"evidence_ref": " "},
            {"source_ref": " "},
        ]:
            candidates.append((positive_id, 2, changes))
        high = publish_tax_rule(
            setup,
            tenant_id=tenant_id,
            command=replace(command.replacement, version=10000),
        )
        high_id = high.id
        _, other_rule, _, _ = _setup(setup)
        other_rule_id = other_rule.id
        setup.commit()
    finally:
        setup.close()
        setup_engine.dispose()
    session, engine = _online_session(
        admin_url if role == "owner" else app_url, tenant_id
    )
    try:
        baseline_rules = session.scalar(
            select(func.count())
            .select_from(helpers.TaxRule)
            .where(helpers.TaxRule.tenant_id == tenant_id)
        )
        for index, (replacement_id, version, changes) in enumerate(candidates):
            trial = replace(
                command,
                replacement=replace(command.replacement, version=version),
                source_ref=f"raw-invalid:{index}",
            )
            with pytest.raises(
                DBAPIError, match="invalid or stale tax rule supersession"
            ):
                _insert_edge_sql(session, tenant_id, trial, replacement_id, **changes)
            session.rollback()
            _reset_tenant(session, tenant_id)
            assert (
                session.scalar(select(func.count()).select_from(TaxRuleSupersession))
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(helpers.TaxRule)
                    .where(helpers.TaxRule.tenant_id == tenant_id)
                )
                == baseline_rules
            )
        for foreign_field in ("predecessor_rule_id", "replacement_rule_id"):
            with pytest.raises(DBAPIError, match="query returned no rows"):
                _insert_edge_sql(
                    session,
                    tenant_id,
                    command,
                    positive_id,
                    **{foreign_field: other_rule_id},
                )
            session.rollback()
            _reset_tenant(session, tenant_id)
            assert (
                session.scalar(select(func.count()).select_from(TaxRuleSupersession))
                == 0
            )
        # Neither side of an edge may refer to a transaction-local unpublished rule.
        for unpublished_side in ("predecessor", "replacement"):
            unpublished = _insert_unpublished_rule(session, tenant_id, predecessor_id)
            if unpublished_side == "replacement":
                trial = replace(
                    command,
                    replacement=replace(command.replacement, version=9000),
                    effective_from=date(2026, 1, 1),
                )
                replacement_id = unpublished
            else:
                trial = replace(
                    command,
                    predecessor_rule_id=unpublished,
                    predecessor_version=9000,
                    replacement=replace(command.replacement, version=10000),
                )
                replacement_id = high_id
            with pytest.raises(
                DBAPIError, match="invalid or stale tax rule supersession"
            ):
                _insert_edge_sql(session, tenant_id, trial, replacement_id)
            session.rollback()
            _reset_tenant(session, tenant_id)
            assert (
                session.scalar(select(func.count()).select_from(TaxRuleSupersession))
                == 0
            )
        # Valid raw SQL is a positive control, and public retry recognizes its content.
        _insert_edge_sql(session, tenant_id, command, positive_id)
        edge = supersede_tax_rule(session, tenant_id=tenant_id, command=command)
        assert edge.replacement.rule_id == positive_id
        assert (
            determine_tax_set(
                session, tenant_id=tenant_id, fact=fact, determined_at=NOW
            )
            == original
        )
        session.commit()
        _reset_tenant(session, tenant_id)
        with pytest.raises(DBAPIError, match="invalid or stale tax rule supersession"):
            _insert_edge_sql(
                session,
                tenant_id,
                replace(
                    command,
                    source_ref="raw-nonleaf",
                    replacement=replace(command.replacement, version=10000),
                ),
                high_id,
            )
        session.rollback()
        _reset_tenant(session, tenant_id)
        if role == "owner":
            # Owner privileges bypass ordinary grants, so these exercise real triggers.
            for statement, expected in [
                (
                    "UPDATE mod_tax.tax_rule_supersessions SET reason = 'changed' "
                    "WHERE tenant_id = :tenant",
                    "append-only",
                ),
                (
                    "UPDATE mod_tax.tax_rule_supersessions SET reason = reason "
                    "WHERE tenant_id = :tenant",
                    "append-only",
                ),
                (
                    "DELETE FROM mod_tax.tax_rule_supersessions "
                    "WHERE tenant_id = :tenant",
                    "append-only",
                ),
                (
                    "DELETE FROM mod_tax.tax_rules WHERE tenant_id = :tenant",
                    "publication is immutable",
                ),
                ("DELETE FROM public.tenants WHERE id = :tenant", None),
            ]:
                with pytest.raises(DBAPIError, match=expected):
                    session.execute(text(statement), {"tenant": tenant_id})
                session.rollback()
                _reset_tenant(session, tenant_id)
                assert (
                    session.scalar(
                        select(func.count()).select_from(TaxRuleSupersession)
                    )
                    == 1
                )
                assert (
                    determine_tax_set(
                        session, tenant_id=tenant_id, fact=fact, determined_at=NOW
                    )
                    == original
                )
        assert supersede_tax_rule(session, tenant_id=tenant_id, command=command) == edge
    finally:
        session.close()
        engine.dispose()
