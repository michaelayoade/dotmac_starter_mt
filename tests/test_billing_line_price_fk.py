# ruff: noqa: S608
"""B1: a rated document line must name its obligation's own price version.

Live PostgreSQL proofs for ``bi_0015``: the composite foreign key
``document_lines(obligation_id, price_source_version)`` ->
``rated_obligations(id, price_version_id)``; credit-note (no obligation) lines
exempt; pre-existing violations leave the constraint NOT VALID with an
exceptions note and are never repaired.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from dotmac_billing.commands import IssueCreditNote
from dotmac_billing.document_line_exceptions import (
    list_document_line_price_exceptions,
)
from dotmac_billing.service import (
    accept_rated_obligation,
    create_billing_account,
    issue_credit_note,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.test_billing_isolation import (
    ASSEMBLY_VERSIONS,
    BILLING_VERSIONS,
    KERNEL_VERSIONS,
    NOW,
    NUMBERING,
    REPO_ROOT,
    _create_tenants,
    _draft_command,
    _money,
    _obligation_command,
    _scratch_database,
    _seed_invoice,
    _session,
)

PLANES = ["tenant", "platform"]
_LINE_SQL = """INSERT INTO mod_billing.{prefix}document_lines
    ({tenant_column}id,billing_account_id,document_id,obligation_id,line_number,
     description,quantity,unit_code,unit_amount,pre_tax_amount,tax_amount,
     total_amount,currency,minor_units,price_source_version)
    VALUES ({tenant_value}:id,:account_id,:document_id,:obligation_id,:line_number,
     'extra',1,'month',100,100,20,120,'NGN',2,:version)"""


def _selection(plane: str) -> ModulePlane:
    return ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM


def _scope(plane: str, admin_url: str):
    return (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )


def _insert_line(session, plane, scope, **values) -> None:
    prefix = "" if plane == "tenant" else "platform_"
    session.execute(
        text(
            _LINE_SQL.format(
                prefix=prefix,
                tenant_column="tenant_id," if plane == "tenant" else "",
                tenant_value=":tenant_id," if plane == "tenant" else "",
            )
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": getattr(scope, "tenant_id", None),
            "line_number": 2,
            **values,
        },
    )


def _three_obligations_one_draft(session, scope):
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=f"fk-{uuid.uuid4()}",
        currency="NGN",
        minor_units=2,
    )
    kinds = frozenset({"recurring_charge_occurrence"})
    first = accept_rated_obligation(
        session,
        scope=scope,
        command=_obligation_command(account.id, scope=scope, source_fact_id="fk-1"),
        accepted_source_kinds=kinds,
    )
    second = accept_rated_obligation(
        session,
        scope=scope,
        command=_obligation_command(account.id, scope=scope, source_fact_id="fk-2"),
        accepted_source_kinds=kinds,
    )
    third = accept_rated_obligation(
        session,
        scope=scope,
        command=_obligation_command(account.id, scope=scope, source_fact_id="fk-3"),
        accepted_source_kinds=kinds,
    )
    from dotmac_billing.service import create_draft_document

    draft = create_draft_document(
        session, scope=scope, command=_draft_command(first.id)
    )
    session.commit()
    return account, first, second, third, draft


def _constraint(admin_url: str, plane: str):
    table = "document_lines" if plane == "tenant" else "platform_document_lines"
    name = f"fk_{table}_obligation_price"
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT c.convalidated, obj_description(c.oid, 'pg_constraint') "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'mod_billing' AND c.conname = :name"
            ),
            {"name": name},
        ).one_or_none()
    engine.dispose()
    return row


@pytest.mark.parametrize("plane", PLANES)
def test_clean_database_has_a_validated_key_and_refuses_a_mismatched_version(plane):
    with _scratch_database((_selection(plane),)) as (admin_url, tenant_url, platform):
        constraint = _constraint(admin_url, plane)
        assert constraint is not None and constraint[0] is True
        scope = _scope(plane, admin_url)
        url = tenant_url if plane == "tenant" else platform
        with _session(url, scope) as session:
            account, first, second, third, draft = _three_obligations_one_draft(
                session, scope
            )
            # The line the service wrote names the obligation's own version.
            _insert_line(
                session,
                plane,
                scope,
                account_id=account.id,
                document_id=draft.id,
                obligation_id=second.id,
                version=second.price_version_id,
            )
            session.commit()
            with pytest.raises(IntegrityError, match="obligation_price"):
                _insert_line(
                    session,
                    plane,
                    scope,
                    account_id=account.id,
                    document_id=draft.id,
                    obligation_id=third.id,
                    version="price:other",
                    line_number=3,
                )
            session.rollback()


@pytest.mark.parametrize("plane", PLANES)
def test_invoice_line_without_an_obligation_is_refused(plane):
    """The exemption is the credit-note document, not a NULL obligation_id."""
    with _scratch_database((_selection(plane),)) as (admin_url, tenant_url, platform):
        scope = _scope(plane, admin_url)
        url = tenant_url if plane == "tenant" else platform
        with _session(url, scope) as session:
            account, _first, _second, _third, draft = _three_obligations_one_draft(
                session, scope
            )
            for number, version in ((2, "price:v1"), (3, f"credit:{draft.id}")):
                with pytest.raises(IntegrityError, match="rated obligation"):
                    _insert_line(
                        session,
                        plane,
                        scope,
                        account_id=account.id,
                        document_id=draft.id,
                        obligation_id=None,
                        version=version,
                        line_number=number,
                    )
                session.rollback()


@pytest.mark.parametrize("plane", PLANES)
def test_credit_note_line_the_service_writes_is_allowed(plane):
    """Credit-note exemption: no obligation, ``credit:<credited document>``."""
    with _scratch_database((_selection(plane),)) as (admin_url, tenant_url, platform):
        scope = _scope(plane, admin_url)
        url = tenant_url if plane == "tenant" else platform
        with _session(url, scope) as session:
            _account, _obligation, invoice = _seed_invoice(session, scope)
            credit = issue_credit_note(
                session,
                scope=scope,
                command=IssueCreditNote(
                    original_document_id=invoice.id,
                    pre_tax_amount=_money("20"),
                    tax_amount=_money("0"),
                    total_amount=_money("20"),
                    reason="service correction",
                    series_code="credit",
                    reference_date=date(2026, 8, 17),
                    actor_ref="billing-test",
                    correlation_id=f"credit:{invoice.id}",
                    occurred_at=NOW,
                    document_profile_code="credit.standard",
                    document_profile_version="1",
                ),
                numbering=NUMBERING,
            )
            session.commit()
            prefix = "" if plane == "tenant" else "platform_"
            row = session.execute(
                text(
                    "SELECT obligation_id, price_source_version FROM "
                    f"mod_billing.{prefix}document_lines WHERE document_id = :d"
                ),
                {"d": credit.id},
            ).one()
            assert row[0] is None and row[1] == f"credit:{invoice.id}"


@pytest.mark.parametrize("plane", PLANES)
def test_existing_violation_stays_not_valid_noted_and_unrepaired(plane):
    from alembic import command as alembic_command
    from alembic.config import Config

    with _scratch_database(
        (_selection(plane),), target_revision="bi_0014_opening_variance_ledger"
    ) as (admin_url, tenant_url, platform):
        scope = _scope(plane, admin_url)
        url = tenant_url if plane == "tenant" else platform
        with _session(url, scope) as session:
            account, _first, second, third, draft = _three_obligations_one_draft(
                session, scope
            )
            _insert_line(
                session,
                plane,
                scope,
                account_id=account.id,
                document_id=draft.id,
                obligation_id=second.id,
                version="price:history-mismatch",
            )
            # A NULL-obligation invoice line, legal before bi_0015.
            _insert_line(
                session,
                plane,
                scope,
                account_id=account.id,
                document_id=draft.id,
                obligation_id=None,
                version="price:history-null",
                line_number=3,
            )
            draft_id, third_id, account_id = draft.id, third.id, account.id
            session.commit()
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {BILLING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=(_selection(plane),)),
        )
        import os

        old = os.environ.get("MIGRATION_DATABASE_URL")
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        try:
            alembic_command.upgrade(config, "bi_0015_line_price_version_fk")
        finally:
            if old is None:
                os.environ.pop("MIGRATION_DATABASE_URL", None)
            else:
                os.environ["MIGRATION_DATABASE_URL"] = old
        validated, note = _constraint(admin_url, plane)
        assert validated is False
        assert "exceptions review" in note and "no repair" in note
        with _session(url, scope) as session:
            prefix = "" if plane == "tenant" else "platform_"
            versions = set(
                session.execute(
                    text(
                        f"SELECT price_source_version FROM mod_billing.{prefix}"
                        "document_lines WHERE document_id = :d"
                    ),
                    {"d": draft_id},
                ).scalars()
            )
            # Unrepaired history, and the key still refuses new violations.
            assert {"price:history-mismatch", "price:history-null"} <= versions
            # The upgrade added the guard without rejecting either row, and the
            # review listing names both, read-only.
            listed = {
                item.price_source_version: item.reason
                for item in list_document_line_price_exceptions(session, scope=scope)
                if item.document_id == draft_id
            }
            assert listed == {
                "price:history-mismatch": "price_version_mismatch",
                "price:history-null": "obligation_required",
            }
            assert (
                set(
                    session.execute(
                        text(
                            f"SELECT price_source_version FROM mod_billing.{prefix}"
                            "document_lines WHERE document_id = :d"
                        ),
                        {"d": draft_id},
                    ).scalars()
                )
                == versions
            )
            with pytest.raises(IntegrityError, match="rated obligation"):
                _insert_line(
                    session,
                    plane,
                    scope,
                    account_id=account_id,
                    document_id=draft_id,
                    obligation_id=None,
                    version="price:new-null",
                    line_number=4,
                )
            session.rollback()
            with pytest.raises(IntegrityError, match="obligation_price"):
                _insert_line(
                    session,
                    plane,
                    scope,
                    account_id=account_id,
                    document_id=draft_id,
                    obligation_id=third_id,
                    version="price:new-mismatch",
                    line_number=5,
                )
            session.rollback()


@pytest.mark.parametrize("plane", PLANES)
def test_issued_invoice_flow_is_unchanged(plane):
    with _scratch_database((_selection(plane),)) as (admin_url, tenant_url, platform):
        scope = _scope(plane, admin_url)
        url = tenant_url if plane == "tenant" else platform
        with _session(url, scope) as session:
            _account, obligation, issued = _seed_invoice(session, scope)
            assert issued.issued_at is not None
            assert obligation.price_version_id == "price:v1"
