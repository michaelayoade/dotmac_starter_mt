"""Hosted PostgreSQL proofs for separately bounded noncash credit evidence."""

from __future__ import annotations

import hashlib

# All interpolated identifiers are fixed module planes/tables.
# ruff: noqa: S608
import importlib.util
import shutil
import subprocess
import time
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from threading import Event, Thread
from uuid import uuid4

import pytest
from dotmac_billing import models
from dotmac_billing.service import grant_non_cash_credit
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    install_module_plane_selections,
    installed_module_plane_selections,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests import test_billing_isolation as billing
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_withholding_persistence as predecessor

REVISION = "bi_0005_non_cash_credit"
MIGRATION = billing.BILLING_VERSIONS / f"{REVISION}.py"


def _models(plane):
    return (
        (
            models.NonCashCreditGrant,
            models.NonCashCreditEffect,
            models.ReceivablePositionFactV2,
            models.PostingGroup,
            models.PostingEffect,
            models.ReceivablePositionFact,
        )
        if plane == "tenant"
        else (
            models.PlatformNonCashCreditGrant,
            models.PlatformNonCashCreditEffect,
            models.PlatformReceivablePositionFactV2,
            models.PlatformPostingGroup,
            models.PlatformPostingEffect,
            models.PlatformReceivablePositionFact,
        )
    )


def _seed(scratch, plane, *, restricted=False):
    scope, url = runtime._scope_url(scratch, plane)
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(session, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            session,
            scope=scope,
            command=runtime._grant(
                scope, account.id, document=invoice.id if restricted else None
            ),
        )
        session.commit()
        account_id, invoice_id, grant_id = account.id, invoice.id, grant.grant_id
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        table = _models(plane)[0].__table__
        row = dict(
            connection.execute(select(table).where(table.c.id == grant_id))
            .mappings()
            .one()
        )
    return engine, scope, account_id, invoice_id, row


def _clean(row):
    return {key: deepcopy(value) for key, value in row.items() if key != "created_at"}


def _group(connection, plane, row, kind):
    table = _models(plane)[3].__table__
    version = (
        connection.scalar(
            select(func.max(table.c.source_version)).where(
                table.c.billing_account_id == row["billing_account_id"]
            )
        )
        or 0
    )
    result = {
        "id": uuid4(),
        "billing_account_id": row["billing_account_id"],
        "group_kind": kind,
        "currency": row["currency"],
        "minor_units": row["minor_units"],
        "source_ref": row["source_ref"],
        "source_version": version + 1,
        "reverses_group_id": None,
        "occurred_at": billing.NOW,
    }
    if "tenant_id" in row:
        result["tenant_id"] = row["tenant_id"]
    return result


def _effect(
    connection,
    plane,
    grant,
    document,
    *,
    kind="application",
    amount="-10",
    original=None,
):
    table = _models(plane)[1].__table__
    template = (
        connection.execute(
            select(table).where(
                table.c.grant_id == grant["id"], table.c.effect_kind == "grant"
            )
        )
        .mappings()
        .one()
    )
    row = _clean(template)
    row.update(
        id=uuid4(),
        source_ref=str(uuid4()),
        effect_kind=kind,
        availability_delta=Decimal(amount),
        document_id=document
        if kind in ("application", "application_reversal")
        else None,
        original_application_id=original,
    )
    row["evidence"].update(
        effect_id=str(row["id"]),
        source_ref=row["source_ref"],
        effect_kind=kind,
        document_id=str(row["document_id"]) if row["document_id"] else None,
        original_application_id=str(original) if original else None,
    )
    row["evidence"]["availability_delta"]["amount"] = str(row["availability_delta"])
    row["evidence_digest"] = fingerprint_of(row["evidence"])
    kind_group = {
        "application": "non_cash_credit_applied",
        "application_reversal": "non_cash_credit_application_reversed",
        "unused_reduction": "non_cash_credit_reduced",
        "grant": "non_cash_credit_granted",
    }[kind]
    posting = _group(connection, plane, row, kind_group)
    row["posting_group_id"] = posting["id"]
    return row, posting


def _snapshots(connection, plane, posting, *, omit_v2=False, wrong=False):
    _, noncash, v2, _, posting_effect, v1 = _models(plane)
    account = posting["billing_account_id"]
    latest = (
        connection.execute(
            select(v1.__table__)
            .where(v1.billing_account_id == account)
            .order_by(v1.source_version.desc())
            .limit(1)
        )
        .mappings()
        .one()
    )
    position = _clean(latest)
    effects = (
        connection.execute(
            select(posting_effect.__table__).where(
                posting_effect.billing_account_id == account
            )
        )
        .mappings()
        .all()
    )
    totals = {
        key: sum(
            (row["amount_delta"] for row in effects if row["lane"] == lane),
            Decimal("0"),
        )
        for key, lane in (
            ("collectible_receivable", "receivable"),
            ("available_credit", "available_credit"),
            ("prepaid_funding", "prepaid_funding"),
        )
    }
    digest = fingerprint_of(
        {
            **{key: format(value, "f") for key, value in totals.items()},
            "currency": posting["currency"],
            "minor_units": posting["minor_units"],
        }
    )
    position.update(
        id=uuid4(),
        posting_group_watermark=posting["id"],
        source_version=posting["source_version"],
        **totals,
        state_fingerprint=digest,
        rebuild_fingerprint=digest,
        financial_state="resolved" if totals["collectible_receivable"] == 0 else "open",
    )
    connection.execute(v1.__table__.insert().values(**position))
    if not omit_v2:
        rows = (
            connection.execute(
                select(noncash.__table__)
                .where(noncash.billing_account_id == account)
                .order_by(noncash.id)
            )
            .mappings()
            .all()
        )
        available = sum((row["availability_delta"] for row in rows), Decimal("0"))
        digest = fingerprint_of(
            {
                "contract": "billing.receivable.position.v2",
                "v1_fingerprint": digest,
                "non_cash_effects": [
                    {
                        "id": str(row["id"]),
                        "posting_group_id": str(row["posting_group_id"]),
                        "availability_delta": str(row["availability_delta"]),
                    }
                    for row in rows
                ],
                "available_non_cash_credit": str(available),
            }
        )
        position.update(
            id=uuid4(),
            available_non_cash_credit=available + (1 if wrong else 0),
            state_fingerprint=digest,
            rebuild_fingerprint=digest,
        )
        connection.execute(v2.__table__.insert().values(**position))


def _insert_effect(connection, plane, row, posting, *, omit_v2=False, wrong=False):
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    _, effect, _, group, ar, _ = _models(plane)
    connection.execute(group.__table__.insert().values(**posting))
    connection.execute(effect.__table__.insert().values(**row))
    if row["document_id"] is not None:
        values = {
            key: row[key]
            for key in (
                "billing_account_id",
                "posting_group_id",
                "currency",
                "minor_units",
            )
        }
        values.update(
            id=uuid4(), lane="receivable", amount_delta=row["availability_delta"]
        )
        if "tenant_id" in row:
            values["tenant_id"] = row["tenant_id"]
        connection.execute(ar.__table__.insert().values(**values))
    _snapshots(connection, plane, posting, omit_v2=omit_v2, wrong=wrong)


def _replacement(connection, plane, original, reduction_id, *, amount="10"):
    grant = _clean(original)
    grant.update(
        id=uuid4(),
        original_amount=Decimal(amount),
        source_ref=str(uuid4()),
        replaces_grant_id=original["id"],
        replaces_effect_id=reduction_id,
    )
    grant["evidence"].update(
        grant_id=str(grant["id"]),
        source_ref=grant["source_ref"],
        replaces_grant_id=str(original["id"]),
        replaces_effect_id=str(reduction_id),
    )
    grant["evidence"]["original_amount"]["amount"] = amount
    grant["evidence_digest"] = fingerprint_of(grant["evidence"])
    posting = _group(connection, plane, grant, "non_cash_credit_granted")
    grant["posting_group_id"] = posting["id"]
    effect, _ = _effect(connection, plane, original, None, kind="grant", amount=amount)
    effect.update(
        grant_id=grant["id"],
        posting_group_id=posting["id"],
        source_ref=grant["source_ref"],
    )
    effect["evidence"].update(grant_id=str(grant["id"]), source_ref=grant["source_ref"])
    effect["evidence_digest"] = fingerprint_of(effect["evidence"])
    return grant, effect, posting


def _insert_grant(connection, plane, grant, effect, posting):
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    connection.execute(_models(plane)[3].__table__.insert().values(**posting))
    connection.execute(_models(plane)[0].__table__.insert().values(**grant))
    connection.execute(_models(plane)[1].__table__.insert().values(**effect))
    _snapshots(connection, plane, posting)


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_new_tables_have_exact_online_grants_and_tenant_rls(scratch, plane):
    prefix = "" if plane == "tenant" else "platform_"
    role = "app_user" if plane == "tenant" else "platform_api"
    wrong = "platform_api" if plane == "tenant" else "app_user"
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        for name in (
            "non_cash_credit_grants",
            "non_cash_credit_effects",
            "receivable_position_facts_v2",
        ):
            table = f"mod_billing.{prefix}{name}"
            assert billing._actual_table_privileges(
                connection, table=table, role=role
            ) == frozenset({"SELECT", "INSERT"})
            assert not billing._actual_table_privileges(
                connection, table=table, role=wrong
            )
            flags = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = to_regclass(:table)"
                ),
                {"table": table},
            ).one()
            assert tuple(flags) == (
                (True, True) if plane == "tenant" else (False, False)
            )
            assert billing._actual_column_privileges(
                connection, role=role, table_name=f"{prefix}{name}", privilege="UPDATE"
            ) == (
                frozenset()
                if name == "receivable_position_facts_v2"
                else frozenset({"id"})
            )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_owner_noop_update_delete_and_online_scope_are_closed(scratch, plane):
    engine, scope, account, _, grant = _seed(scratch, plane)
    with engine.begin() as connection:
        for model in _models(plane)[:3]:
            table = model.__table__
            row_id = connection.scalar(
                select(table.c.id).where(table.c.billing_account_id == account).limit(1)
            )
            for statement in (
                table.update().where(table.c.id == row_id).values(id=row_id),
                table.delete().where(table.c.id == row_id),
            ):
                with pytest.raises(DBAPIError) as error, connection.begin_nested():
                    connection.execute(statement)
                assert error.value.orig.sqlstate == "23001"
        if isinstance(scope, TenantScope):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM public.tenants WHERE id = :id"),
                    {"id": scope.tenant_id},
                )
    if plane == "tenant":
        other = billing._create_tenants(scratch[0], 1)[0]
        hidden = billing._tenant_engine(scratch[1], other)
        with hidden.begin() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(models.NonCashCreditGrant)
                    .where(models.NonCashCreditGrant.id == grant["id"])
                )
                == 0
            )
        hidden.dispose()
    online = (
        billing._tenant_engine(scratch[1], scope.tenant_id)
        if isinstance(scope, TenantScope)
        else create_engine(scratch[2])
    )
    with online.begin() as connection:
        for model in _models(plane)[:2]:
            table = model.__table__
            row_id = connection.scalar(
                select(table.c.id).where(table.c.billing_account_id == account).limit(1)
            )
            # The narrowly granted column permits the real invoker row lock.
            assert (
                connection.scalar(
                    select(table.c.id).where(table.c.id == row_id).with_for_update()
                )
                == row_id
            )
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                connection.execute(
                    table.update().where(table.c.id == row_id).values(id=row_id)
                )
            assert error.value.orig.sqlstate == "23001"
    online.dispose()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "fractional",
        "nan",
        "currency",
        "grant_cap",
        "evidence",
        "digest",
        "source_duplicate",
        "group_kind",
        "missing_v2",
        "wrong_v2",
        "backdated_version",
    ],
)
def test_direct_sql_application_and_snapshot_admission(scratch, plane, fault):
    engine, _, _, invoice, grant = _seed(scratch, plane)
    with engine.begin() as connection:
        row, posting = _effect(connection, plane, grant, invoice)
        if fault in ("fractional", "nan", "grant_cap"):
            row["availability_delta"] = {
                "fractional": Decimal("-0.001"),
                "nan": Decimal("NaN"),
                "grant_cap": Decimal("-51"),
            }[fault]
            row["evidence"]["availability_delta"]["amount"] = str(
                row["availability_delta"]
            )
        elif fault == "currency":
            row["currency"] = posting["currency"] = row["evidence"][
                "availability_delta"
            ]["currency"] = "USD"
        elif fault == "evidence":
            row["evidence"]["grant_id"] = str(uuid4())
        elif fault == "digest":
            row["request_digest"] = "not-a-digest"
        elif fault == "source_duplicate":
            original = (
                connection.execute(
                    select(_models(plane)[1].__table__).where(
                        _models(plane)[1].grant_id == grant["id"]
                    )
                )
                .mappings()
                .one()
            )
            row["source_ref"] = posting["source_ref"] = row["evidence"][
                "source_ref"
            ] = original["source_ref"]
        elif fault == "group_kind":
            posting["group_kind"] = "allocation"
        elif fault == "backdated_version":
            posting["source_version"] -= 1
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            _insert_effect(
                connection,
                plane,
                row,
                posting,
                omit_v2=fault == "missing_v2",
                wrong=fault == "wrong_v2",
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate in {"23514", "23505"}
        if fault == "backdated_version":
            assert error.value.orig.sqlstate == "23514"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "fractional",
        "nan",
        "digest",
        "classification",
        "source_duplicate",
        "replacement_pair",
    ],
)
def test_grant_envelope_and_identity_are_enforced_by_owner_sql(scratch, plane, fault):
    engine, _, _, _, original = _seed(scratch, plane)
    with engine.begin() as connection:
        grant, effect, group = _replacement(connection, plane, original, uuid4())
        grant.update(replaces_grant_id=None, replaces_effect_id=None)
        grant["evidence"].update(replaces_grant_id=None, replaces_effect_id=None)
        if fault in ("fractional", "nan"):
            value = Decimal("0.001" if fault == "fractional" else "NaN")
            grant["original_amount"] = effect["availability_delta"] = value
            grant["evidence"]["original_amount"]["amount"] = str(value)
            effect["evidence"]["availability_delta"]["amount"] = str(value)
        elif fault == "digest":
            grant["config_digest"] = grant["evidence"]["classification"][
                "config_digest"
            ] = "bad"
        elif fault == "classification":
            grant["evidence"]["classification"]["accepted_snapshot"] = [
                {"name": "reason", "value": "a"},
                {"name": "reason", "value": "b"},
            ]
        elif fault == "source_duplicate":
            grant["source_ref"] = grant["evidence"]["source_ref"] = group[
                "source_ref"
            ] = original["source_ref"]
            effect["source_ref"] = effect["evidence"]["source_ref"] = original[
                "source_ref"
            ]
        elif fault == "replacement_pair":
            grant["replaces_grant_id"] = original["id"]
            grant["evidence"]["replaces_grant_id"] = str(original["id"])
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            _insert_grant(connection, plane, grant, effect, group)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate in {"23514", "23505"}
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_sql_owner_rollback_removes_entire_new_application_and_snapshots(
    scratch, plane
):
    engine, _, account, invoice, grant = _seed(scratch, plane)

    def counts():
        with engine.connect() as connection:
            return {
                model.__table__.name: connection.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.billing_account_id == account)
                )
                for model in _models(plane)
            }

    before = counts()
    with engine.connect() as connection:
        transaction = connection.begin()
        row, group = _effect(connection, plane, grant, invoice)
        _insert_effect(connection, plane, row, group)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        transaction.rollback()
    assert counts() == before
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_partial_reversal_cumulative_caps_and_unused_only_reduction(scratch, plane):
    engine, _, _, invoice, grant = _seed(scratch, plane)
    with engine.begin() as connection:
        applied, posting = _effect(connection, plane, grant, invoice, amount="-30")
        _insert_effect(connection, plane, applied, posting)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            reduced, group = _effect(
                connection, plane, grant, None, kind="unused_reduction", amount="-21"
            )
            _insert_effect(connection, plane, reduced, group)
        assert error.value.orig.sqlstate == "23514"
        reversed_row, group = _effect(
            connection,
            plane,
            grant,
            invoice,
            kind="application_reversal",
            amount="10",
            original=applied["id"],
        )
        _insert_effect(connection, plane, reversed_row, group)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        for amount, target in (("21", applied["id"]), ("1", reversed_row["id"])):
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                row, group = _effect(
                    connection,
                    plane,
                    grant,
                    invoice,
                    kind="application_reversal",
                    amount=amount,
                    original=target,
                )
                _insert_effect(connection, plane, row, group)
            assert error.value.orig.sqlstate == "23514"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_sql_application_cannot_ignore_withholding_or_invoice_restriction(
    scratch, plane
):
    from dotmac_billing.service import accept_withholding_discharge

    engine, scope, account, invoice, grant = _seed(scratch, plane, restricted=True)
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        accept_withholding_discharge(
            session,
            scope=scope,
            command=billing._withholding_command(
                account,
                invoice,
                scope=scope,
                suffix=str(uuid4()),
                claimed="100",
                accepted="100",
            ),
        )
        session.commit()
    with engine.begin() as connection:
        # Grant room is fifty, but after WHT the invoice has only twenty AR.
        for document, amount in ((invoice, "-21"), (uuid4(), "-1")):
            row, group = _effect(connection, plane, grant, document, amount=amount)
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                _insert_effect(connection, plane, row, group)
            assert error.value.orig.sqlstate == "23514"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_replacement_is_bounded_to_named_unused_reduction(scratch, plane):
    engine, _, _, _, grant = _seed(scratch, plane)
    with engine.begin() as connection:
        reduced, group = _effect(
            connection, plane, grant, None, kind="unused_reduction", amount="-20"
        )
        _insert_effect(connection, plane, reduced, group)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        replacement, effect, group = _replacement(
            connection, plane, grant, reduced["id"], amount="12"
        )
        _insert_grant(connection, plane, replacement, effect, group)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            replacement, effect, group = _replacement(
                connection, plane, grant, reduced["id"], amount="9"
            )
            _insert_grant(connection, plane, replacement, effect, group)
        assert error.value.orig.sqlstate == "23514"
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            replacement, effect, group = _replacement(
                connection, plane, grant, uuid4(), amount="1"
            )
            _insert_grant(connection, plane, replacement, effect, group)
        assert error.value.orig.sqlstate in {"23514", "23503"}
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_active_application_blocks_issuance_reversal_and_void_and_late_group_edits(
    scratch, plane
):
    engine, _, account, invoice, grant = _seed(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    with engine.begin() as connection:
        applied, posting = _effect(connection, plane, grant, invoice, amount="-10")
        _insert_effect(connection, plane, applied, posting)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with engine.begin() as connection:
        issuance = connection.scalar(
            select(_models(plane)[3].id).where(
                _models(plane)[3].billing_account_id == account,
                _models(plane)[3].group_kind == "invoice_issued",
            )
        )
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            group = _group(connection, plane, grant, "reversal")
            group["reverses_group_id"] = issuance
            connection.execute(_models(plane)[3].__table__.insert().values(**group))
        assert error.value.orig.sqlstate == "23514"
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    f"INSERT INTO mod_billing.{prefix}document_events "
                    f"(id, {'tenant_id,' if plane == 'tenant' else ''}document_id, "
                    "event_kind, actor_ref, occurred_at) "
                    f"VALUES (:id, {':tenant,' if plane == 'tenant' else ''}:document, "
                    "'voided', 'owner', :now)"
                ),
                {
                    "id": uuid4(),
                    "tenant": grant.get("tenant_id"),
                    "document": invoice,
                    "now": billing.NOW,
                },
            )
        assert error.value.orig.sqlstate == "23514"
        # A late, otherwise valid auto-application cannot change the old group's
        # immutable position snapshots, even with the matching AR effect.
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            late, _ = _effect(connection, plane, grant, invoice, amount="-1")
            late.update(
                posting_group_id=grant["posting_group_id"],
                source_owner="dotmac-billing",
                source_ref=f"apply-on-issue:{grant['id']}",
            )
            late["evidence"].update(
                source_owner=late["source_owner"], source_ref=late["source_ref"]
            )
            connection.execute(_models(plane)[1].__table__.insert().values(**late))
            ar = {
                key: late[key]
                for key in (
                    "posting_group_id",
                    "billing_account_id",
                    "currency",
                    "minor_units",
                )
            }
            ar.update(id=uuid4(), lane="receivable", amount_delta=Decimal("-1"))
            if "tenant_id" in grant:
                ar["tenant_id"] = grant["tenant_id"]
            connection.execute(_models(plane)[4].__table__.insert().values(**ar))
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate == "23514"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("later", ["withholding", "allocation", "credit_note"])
def test_later_resolution_cannot_overdraw_invoice_masked_by_second_invoice(
    scratch, plane, later
):
    engine, scope, account, invoice, grant = _seed(scratch, plane)
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        draft = billing._additional_credit_invoice(
            session, scope, account, str(uuid4())
        )
        billing.issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        runtime.apply_non_cash_credit(
            session,
            scope=scope,
            command=runtime._apply(scope, grant["id"], invoice, "50"),
        )
        settlement = (
            billing.accept_settlement(
                session,
                scope=scope,
                command=billing._settlement_command(
                    account, scope=scope, amount="80", source_key=str(uuid4())
                ),
            )
            if later == "allocation"
            else None
        )
        settlement_id = settlement.id if settlement else None
        other_invoice_id = draft.id
        session.commit()
    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            if later == "withholding":
                row, group = predecessor._row(
                    connection, plane, account, invoice, grant.get("tenant_id")
                )
                row.update(claimed_amount=Decimal("80"), amount_delta=Decimal("80"))
                for name in ("claimed_amount", "accepted_delta"):
                    row["evidence"][name]["amount"] = "80"
                row["evidence"]["tax_evidence"]["amount"]["amount"] = "80"
                row["evidence"]["tax_evidence"]["lines"][0]["tax_amount"]["amount"] = (
                    "80"
                )
                predecessor._insert(connection, plane, row, group)
            elif later == "allocation":
                group = _group(connection, plane, grant, "allocation")
                connection.execute(_models(plane)[3].__table__.insert().values(**group))
                allocation = (
                    models.AllocationEffect
                    if plane == "tenant"
                    else models.PlatformAllocationEffect
                ).__table__
                values = {
                    "id": uuid4(),
                    "billing_account_id": account,
                    "posting_group_id": group["id"],
                    "settlement_id": settlement_id,
                    "document_id": invoice,
                    "effect_kind": "allocation",
                    "amount_delta": Decimal("80"),
                    "currency": "NGN",
                    "minor_units": 2,
                }
                if "tenant_id" in grant:
                    values["tenant_id"] = grant["tenant_id"]
                connection.execute(allocation.insert().values(**values))
                for lane in ("receivable", "available_credit"):
                    effect = {
                        key: values[key]
                        for key in (
                            "billing_account_id",
                            "posting_group_id",
                            "currency",
                            "minor_units",
                        )
                    }
                    effect.update(id=uuid4(), lane=lane, amount_delta=Decimal("-80"))
                    if "tenant_id" in grant:
                        effect["tenant_id"] = grant["tenant_id"]
                    connection.execute(
                        _models(plane)[4].__table__.insert().values(**effect)
                    )
                _snapshots(connection, plane, group)
            else:
                documents = (
                    models.BillingDocument
                    if plane == "tenant"
                    else models.PlatformBillingDocument
                ).__table__
                original = (
                    connection.execute(
                        select(documents).where(documents.c.id == invoice)
                    )
                    .mappings()
                    .one()
                )
                note = _clean(original)
                note.update(
                    id=uuid4(),
                    document_kind="credit_note",
                    credits_document_id=invoice,
                    subtotal=Decimal("80"),
                    tax_total=Decimal("0"),
                    grand_total=Decimal("80"),
                    document_number=str(uuid4()),
                )
                connection.execute(documents.insert().values(**note))
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate == "23514"
        assert "invoice resolution exceeds" in str(error.value.orig)
    # The failed eighty attempt rolled back completely. The exact remaining
    # seventy must still be admitted through the same current financial owner.
    from dotmac_billing.commands import AllocationCommand, IssueCreditNote
    from dotmac_billing.queries import get_receivable_position_v2
    from dotmac_billing.service import (
        _document_outstanding,
        accept_withholding_discharge,
        allocate_settlement,
        issue_credit_note,
    )

    with billing._session(url, scope) as session:
        if later == "withholding":
            accepted = accept_withholding_discharge(
                session,
                scope=scope,
                command=billing._withholding_command(
                    account,
                    invoice,
                    scope=scope,
                    suffix=str(uuid4()),
                    claimed="70",
                    accepted="70",
                ),
            )
            assert accepted.document_id == invoice
            assert accepted.amount_delta == Decimal("70")
            assert accepted.claimed_amount == Decimal("70")
            final_group_id = accepted.posting_group_id
        elif later == "allocation":
            allocated = allocate_settlement(
                session,
                scope=scope,
                command=AllocationCommand(
                    settlement_id,
                    invoice,
                    billing._money("70"),
                    billing.NOW,
                    str(uuid4()),
                ),
            )
            allocation_model = (
                models.AllocationEffect
                if plane == "tenant"
                else models.PlatformAllocationEffect
            )
            evidence = session.scalars(
                select(allocation_model).where(
                    allocation_model.posting_group_id == allocated.id
                )
            ).one()
            assert evidence.document_id == invoice
            assert evidence.settlement_id == settlement_id
            assert evidence.amount_delta == Decimal("70")
            final_group_id = allocated.id
        else:
            accepted = issue_credit_note(
                session,
                scope=scope,
                command=IssueCreditNote(
                    original_document_id=invoice,
                    pre_tax_amount=billing._money("70"),
                    tax_amount=billing._money("0"),
                    total_amount=billing._money("70"),
                    reason="exact remaining correction",
                    series_code="credit",
                    reference_date=billing.NOW.date(),
                    actor_ref="finance:fixture",
                    correlation_id=str(uuid4()),
                    occurred_at=billing.NOW,
                    document_profile_code="credit.standard",
                    document_profile_version="1",
                ),
                numbering=billing.NUMBERING,
            )
            assert accepted.credits_document_id == invoice
            assert accepted.subtotal == accepted.grand_total == Decimal("70")
            assert accepted.tax_total == 0
            assert accepted.issued_at is not None
            final_group_id = session.scalars(
                select(_models(plane)[3].id).where(
                    _models(plane)[3].source_ref == f"credit_note:{accepted.id}"
                )
            ).one()
        session.commit()
        document_model = (
            models.BillingDocument
            if plane == "tenant"
            else models.PlatformBillingDocument
        )
        target = session.get(document_model, invoice)
        other = session.get(document_model, other_invoice_id)
        assert _document_outstanding(session, scope=scope, document=target) == 0
        assert _document_outstanding(session, scope=scope, document=other) == Decimal(
            "120"
        )
        position = billing.rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account,
            currency="NGN",
            minor_units=2,
        )
        assert position.collectible_receivable == Decimal("120")
        assert position.available_credit == (
            Decimal("10") if later == "allocation" else 0
        )
        assert position.prepaid_funding == 0
        final_group = session.get(_models(plane)[3], final_group_id)
        snapshot = get_receivable_position_v2(
            session,
            scope=scope,
            billing_account_id=account,
            source_version=final_group.source_version,
        )
        assert snapshot.collectible_receivable.amount == position.collectible_receivable
        assert snapshot.available_credit.amount == position.available_credit
        assert snapshot.prepaid_funding.amount == 0
        assert snapshot.available_non_cash_credit.amount == 0
        noncash = session.scalars(
            select(_models(plane)[1]).where(_models(plane)[1].grant_id == grant["id"])
        ).all()
        assert sorted(effect.availability_delta for effect in noncash) == [
            Decimal("-50"),
            Decimal("50"),
        ]
    engine.dispose()


def _module():
    spec = importlib.util.spec_from_file_location(
        "billing_noncash_migration_canary", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_account_lock_serializes_competing_sql_applications_before_cap_admission(
    scratch, plane
):
    engine, _, _, invoice, grant = _seed(scratch, plane)
    started = Event()
    pid = []
    result = []

    def contender():
        try:
            with engine.begin() as connection:
                pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
                started.set()
                _insert_effect(connection, plane, candidate, candidate_group)
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            result.append("unexpected second application")
        except DBAPIError as error:
            result.append(error.orig.sqlstate)
        except Exception as error:
            result.append(error)

    with engine.begin() as first:
        row, group = _effect(first, plane, grant, invoice, amount="-40")
        _insert_effect(first, plane, row, group)
        first.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        # Prepare the next valid group while the first transaction sees its own
        # insert. The contender performs no explicit helper lock or max probe.
        candidate, candidate_group = _effect(first, plane, grant, invoice, amount="-40")
        thread = Thread(target=contender)
        thread.start()
        assert started.wait(timeout=10)
        deadline = time.monotonic() + 10
        observed = False
        with engine.connect() as observer:
            while time.monotonic() < deadline:
                observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                waiting = observer.execute(
                    text(
                        "SELECT wait_event_type, query FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": pid[0]},
                ).one()
                if (
                    waiting.wait_event_type == "Lock"
                    and "insert into mod_billing." in waiting.query.lower()
                ):
                    observed = True
                    break
                time.sleep(0.02)
        assert observed, "contender did not wait on the held account row"
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert result == ["23514"]
    with engine.connect() as connection:
        table = _models(plane)[1].__table__
        assert connection.scalar(
            select(func.sum(table.c.availability_delta)).where(
                table.c.grant_id == grant["id"]
            )
        ) == Decimal("10")
    engine.dispose()


@contextmanager
def _migration(connection, planes):
    previous = installed_module_plane_selections()
    install_module_plane_selections(
        (ModulePlaneSelection(module="billing", planes=planes),)
    )
    try:
        with Operations.context(MigrationContext.configure(connection)):
            yield _module()
    finally:
        install_module_plane_selections(previous)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_populated_downgrade_refuses_globally_without_dropping_guards(scratch, plane):
    engine, _, _, _, _ = _seed(scratch, plane)
    with engine.begin() as connection:
        before = predecessor._catalog(connection)
        connection.execute(
            text(
                "SET LOCAL lock_timeout = '137ms'; "
                "SET LOCAL statement_timeout = '19s'"
            )
        )
        with _migration(
            connection, (ModulePlane.TENANT, ModulePlane.PLATFORM)
        ) as migration:
            with (
                pytest.raises(RuntimeError, match="noncash or V2 position"),
                connection.begin_nested(),
            ):
                migration.downgrade()
        assert predecessor._catalog(connection) == before
        assert connection.scalar(text("SHOW lock_timeout")) == "137ms"
        assert connection.scalar(text("SHOW statement_timeout")) == "19s"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_published_predecessor_and_empty_downgrade_preserve_old_evidence(
    tmp_path, monkeypatch, plane
):
    git = shutil.which("git")
    assert git
    path = (
        "packages/dotmac-billing/src/dotmac_billing/migrations/versions/"
        "bi_0001_billing.py"
    )
    source = subprocess.run(  # noqa: S603 # nosec B603
        [git, "show", f"{predecessor.RELEASE}:{path}"],
        cwd=billing.REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    assert hashlib.sha256(source).hexdigest() == predecessor.RELEASE_DIGEST
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "bi_0001_billing.py").write_bytes(source)
    for filename in (
        "bi_0002_multiline.py",
        "bi_0003_issued_line_freeze.py",
        "bi_0004_withholding_discharge.py",
        f"{REVISION}.py",
    ):
        shutil.copyfile(billing.BILLING_VERSIONS / filename, versions / filename)
    monkeypatch.setattr(billing, "BILLING_VERSIONS", versions)
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with billing._scratch_database(
        (selection,), target_revision="bi_0001_billing"
    ) as urls:
        tenant = billing._create_tenants(urls[0], 1)[0] if plane == "tenant" else None
        engine = create_engine(urls[0])
        with engine.begin() as connection:
            predecessor._invoice(connection, plane, tenant, legacy=True)
        config = predecessor._config((selection,))
        command.upgrade(config, "bi_0004_withholding_discharge")
        with engine.connect() as connection:
            before = predecessor._catalog(connection)
            prefix = "platform_" if plane == "platform" else ""
            evidence = {
                name: connection.execute(
                    text(
                        f"SELECT to_jsonb(x) FROM mod_billing.{prefix}{name} x "
                        "ORDER BY id"
                    )
                )
                .scalars()
                .all()
                for name in ("billing_accounts", "documents", "document_lines")
            }
        with engine.connect() as writer:
            transaction = writer.begin()
            writer.execute(
                text(
                    f"LOCK TABLE mod_billing."
                    f"{'platform_' if plane == 'platform' else ''}"
                    "billing_accounts IN ROW EXCLUSIVE MODE"
                )
            )
            try:
                with pytest.raises(DBAPIError) as error:
                    command.upgrade(config, REVISION)
                assert error.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = '137ms'; "
                    "SET LOCAL statement_timeout = '19s'"
                )
            )
            with _migration(connection, (selection,)) as migration:
                migration.upgrade()
                assert connection.scalar(text("SHOW lock_timeout")) == "137ms"
                assert connection.scalar(text("SHOW statement_timeout")) == "19s"
                migration.downgrade()
                assert connection.scalar(text("SHOW lock_timeout")) == "137ms"
                assert connection.scalar(text("SHOW statement_timeout")) == "19s"
            assert predecessor._catalog(connection) == before
            assert evidence == {
                name: connection.execute(
                    text(
                        f"SELECT to_jsonb(x) FROM mod_billing.{prefix}{name} x "
                        "ORDER BY id"
                    )
                )
                .scalars()
                .all()
                for name in evidence
            }
        engine.dispose()
