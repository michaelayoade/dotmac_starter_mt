"""Fresh migrated app-user proofs for caller-owned Tax authoring."""

from dataclasses import replace
from threading import Barrier, Thread

import pytest
from dotmac_tax import (
    TaxAuthorityInput,
    TaxConflict,
    TaxNotFound,
    ensure_tax_authority_in_transaction,
    ensure_tax_code_in_transaction,
    ensure_tax_jurisdiction_in_transaction,
    ensure_tax_rule_in_transaction,
)
from dotmac_tax.models import (
    TaxAuthority,
    TaxCode,
    TaxJurisdiction,
    TaxRule,
    TaxRuleBand,
)
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import Session

from alembic import command
from tests import test_tax_released_migration_upgrade as migration_helpers
from tests.unit import test_tax_owner_authoring as owner_helpers
from tests.unit import test_tax_service as tax_helpers

database = migration_helpers.database


@pytest.fixture
def authored_database(database, monkeypatch):
    _, admin_url, app_url = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = migration_helpers._config(migration_helpers.CURRENT_TAX)
    command.upgrade(config, "tax@head")
    engine = create_engine(admin_url)
    with Session(engine) as session:
        first, second = tax_helpers._tenant(session), tax_helpers._tenant(session)
        tenant_ids = first.id, second.id
        session.commit()
    try:
        yield engine, app_url, tenant_ids
    finally:
        engine.dispose()


def _session(engine, tenant_id):
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    return session


def _author(session, tenant_id, kind, inputs, *, changed=False):
    authority_input, _, jurisdiction_input, _, code_input, code = inputs
    if kind == "authority":
        return ensure_tax_authority_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                authority_input, code="RACE", name="Changed" if changed else "Same"
            ),
        )
    if kind == "jurisdiction":
        return ensure_tax_jurisdiction_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                jurisdiction_input, code="RACE", name="Changed" if changed else "Same"
            ),
        )
    if kind == "code":
        return ensure_tax_code_in_transaction(
            session,
            tenant_id=tenant_id,
            **{**code_input, "code": "RACE", "name": "Changed" if changed else "Same"},
        )
    return ensure_tax_rule_in_transaction(
        session,
        tenant_id=tenant_id,
        command=replace(
            owner_helpers.rule_input(code.tax_code_id), priority=11 if changed else 10
        ),
    )


def test_migrated_first_create_replay_cross_tenant_and_complete_owner_rollback(
    authored_database,
):
    admin, app_url, (first, second) = authored_database
    online = create_engine(app_url)
    with _session(online, first) as session:
        with owner_helpers.participant_guard(session):
            inputs = owner_helpers.author_catalog(session, first)
            (
                authority_input,
                authority,
                jurisdiction_input,
                jurisdiction,
                code_input,
                code,
            ) = inputs
            rule_input = owner_helpers.rule_input(code.tax_code_id)
            rule = ensure_tax_rule_in_transaction(
                session, tenant_id=first, command=rule_input
            )
            assert (
                ensure_tax_authority_in_transaction(
                    session, tenant_id=first, command=authority_input
                )
                == authority
            )
            assert (
                ensure_tax_jurisdiction_in_transaction(
                    session, tenant_id=first, command=jurisdiction_input
                )
                == jurisdiction
            )
            assert (
                ensure_tax_code_in_transaction(session, tenant_id=first, **code_input)
                == code
            )
            assert (
                ensure_tax_rule_in_transaction(
                    session, tenant_id=first, command=rule_input
                )
                == rule
            )
        session.commit()  # Only the public caller boundary completes the transaction.
    with _session(online, second) as session:
        with owner_helpers.participant_guard(session):
            assert session.scalar(select(func.count()).select_from(TaxAuthority)) == 0
            with pytest.raises(TaxNotFound):
                ensure_tax_jurisdiction_in_transaction(
                    session, tenant_id=second, command=jurisdiction_input
                )
            with pytest.raises(TaxNotFound):
                ensure_tax_code_in_transaction(session, tenant_id=second, **code_input)
            with pytest.raises(TaxNotFound):
                ensure_tax_rule_in_transaction(
                    session, tenant_id=second, command=rule_input
                )
        session.rollback()
    with _session(online, second) as session:
        with pytest.raises(RuntimeError, match="owner failure after flush"):
            with owner_helpers.participant_guard(session):
                *_, code = owner_helpers.author_catalog(
                    session, second, suffix="rollback"
                )
                ensure_tax_rule_in_transaction(
                    session,
                    tenant_id=second,
                    command=owner_helpers.rule_input(code.tax_code_id),
                )
            raise RuntimeError("owner failure after flush")
        session.rollback()
    with admin.connect() as connection:
        for model in (TaxAuthority, TaxJurisdiction, TaxCode, TaxRule, TaxRuleBand):
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.tenant_id == second)
                )
                == 0
            )
    online.dispose()


@pytest.mark.parametrize("kind", ["authority", "jurisdiction", "code", "rule"])
@pytest.mark.parametrize("changed", [False, True])
def test_unique_insert_loser_retries_whole_owner_transaction(
    authored_database, kind, changed
):
    admin, app_url, (tenant, _) = authored_database
    online = create_engine(app_url)
    with _session(online, tenant) as session:
        with owner_helpers.participant_guard(session):
            inputs = owner_helpers.author_catalog(session, tenant)
        session.commit()
    target = {
        "authority": TaxAuthority,
        "jurisdiction": TaxJurisdiction,
        "code": TaxCode,
        "rule": TaxRule,
    }[kind]
    barrier = Barrier(2)
    outcomes = []
    errors = []

    def author(different):
        session = _session(online, tenant)
        waited = False

        def synchronize_before_insert(current, _context, _instances):
            nonlocal waited
            if not waited and any(isinstance(row, target) for row in current.new):
                waited = True
                barrier.wait(timeout=15)

        event.listen(session, "before_flush", synchronize_before_insert)
        try:
            try:
                with owner_helpers.participant_guard(session):
                    result = _author(session, tenant, kind, inputs, changed=different)
                session.commit()
                outcomes.append(("created", result))
            except IntegrityError as exc:
                assert exc.orig.sqlstate == "23505"
                # The participant propagates the failure; it neither rolls back
                # nor queries the now-aborted transaction to find the winner.
                with pytest.raises(PendingRollbackError):
                    session.scalar(select(func.count()).select_from(target))
                session.rollback()
                session.close()
                session = _session(online, tenant)
                try:
                    with owner_helpers.participant_guard(session):
                        result = _author(
                            session, tenant, kind, inputs, changed=different
                        )
                    session.commit()
                    outcomes.append(("exact_retry", result))
                except TaxConflict:
                    session.rollback()
                    outcomes.append(("conflict_retry", None))
        except Exception as exc:
            errors.append(exc)
            barrier.abort()
        finally:
            session.close()

    threads = [
        Thread(target=author, args=(False,)),
        Thread(target=author, args=(changed,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert sorted(outcome for outcome, _ in outcomes) == (
        ["conflict_retry", "created"] if changed else ["created", "exact_retry"]
    )
    if not changed:
        assert outcomes[0][1] == outcomes[1][1]
    with admin.connect() as connection:
        predicate = target.version == 1 if kind == "rule" else target.code == "RACE"
        assert (
            connection.scalar(
                select(func.count())
                .select_from(target)
                .where(target.tenant_id == tenant, predicate)
            )
            == 1
        )
    online.dispose()


def test_catalog_replay_refreshes_stale_identity_after_committed_catalog_edit(
    authored_database,
):
    admin, app_url, (tenant, _) = authored_database
    online = create_engine(app_url)
    request = TaxAuthorityInput("CURRENT", "Original")
    with _session(online, tenant) as session:
        with owner_helpers.participant_guard(session):
            original = ensure_tax_authority_in_transaction(
                session, tenant_id=tenant, command=request
            )
        cached = session.get(TaxAuthority, original.authority_id)
        session.commit()
        with admin.begin() as connection:
            connection.execute(
                TaxAuthority.__table__.update()
                .where(TaxAuthority.id == original.authority_id)
                .values(name="Current")
            )
        assert cached.name == "Original"
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with owner_helpers.participant_guard(session), pytest.raises(TaxConflict):
            ensure_tax_authority_in_transaction(
                session, tenant_id=tenant, command=request
            )
        assert cached.name == "Current"
        session.rollback()
    online.dispose()


@pytest.mark.parametrize("kind", ["authority", "rule"])
@pytest.mark.parametrize("changed", [False, True])
def test_winner_committed_between_duplicate_probes_replays_exact_current_content(
    authored_database, monkeypatch, kind, changed
):
    """The former wrapper raises on the same-command case at its second probe."""
    _, app_url, (tenant, _) = authored_database
    online = create_engine(app_url)
    with _session(online, tenant) as session:
        with owner_helpers.participant_guard(session):
            inputs = owner_helpers.author_catalog(session, tenant)
        session.commit()
    target = TaxAuthority if kind == "authority" else TaxRule
    winner = []
    with _session(online, tenant) as loser:
        original_scalar = loser.scalar
        interleaved = False

        def commit_winner_after_missing_read(statement, *args, **kwargs):
            nonlocal interleaved
            result = original_scalar(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            if (
                not interleaved
                and descriptions
                and descriptions[0].get("entity") is target
            ):
                assert result is None
                interleaved = True
                # The real first probe has completed with no row. Commit a
                # separate app-user owner before returning that missing result,
                # forcing the established helper's duplicate probe to see it.
                with _session(online, tenant) as author:
                    with owner_helpers.participant_guard(author):
                        winner.append(
                            _author(author, tenant, kind, inputs, changed=changed)
                        )
                    author.commit()
            return result

        monkeypatch.setattr(loser, "scalar", commit_winner_after_missing_read)
        if changed:
            with owner_helpers.participant_guard(loser), pytest.raises(TaxConflict):
                _author(loser, tenant, kind, inputs)
            loser.rollback()
        else:
            with owner_helpers.participant_guard(loser):
                actual = _author(loser, tenant, kind, inputs)
            assert actual == winner[0]
            loser.commit()
        assert interleaved and len(winner) == 1
    online.dispose()


@pytest.mark.parametrize("kind", ["jurisdiction", "code", "rule"])
def test_new_child_admission_refreshes_preloaded_mutable_parent_status(
    authored_database, kind
):
    admin, app_url, (tenant, _) = authored_database
    online = create_engine(app_url)
    with _session(online, tenant) as session:
        with owner_helpers.participant_guard(session):
            inputs = owner_helpers.author_catalog(session, tenant)
        _, authority, _, jurisdiction, _, code = inputs
        model, row_id = {
            "jurisdiction": (TaxAuthority, authority.authority_id),
            "code": (TaxJurisdiction, jurisdiction.jurisdiction_id),
            "rule": (TaxCode, code.tax_code_id),
        }[kind]
        cached = session.get(model, row_id)
        session.commit()
        with admin.begin() as connection:
            connection.execute(
                model.__table__.update()
                .where(model.id == row_id)
                .values(status="retired")
            )
        assert cached.status == "active"
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with (
            owner_helpers.participant_guard(session),
            pytest.raises(TaxConflict, match="retired"),
        ):
            _author(session, tenant, kind, inputs)
        assert cached.status == "retired"
        session.rollback()
    online.dispose()
