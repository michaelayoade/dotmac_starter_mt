"""Exact Tax authoring participates in one caller-owned transaction."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import get_type_hints

import dotmac_tax
import pytest
from dotmac_tax import (
    TaxAuthorityInput,
    TaxAuthorityV1,
    TaxCodeV1,
    TaxConflict,
    TaxJurisdictionInput,
    TaxJurisdictionV1,
    TaxNotFound,
    TaxRuleBandInput,
    TaxRuleInput,
    TaxRuleV1,
    ensure_tax_authority,
    ensure_tax_authority_in_transaction,
    ensure_tax_code_in_transaction,
    ensure_tax_jurisdiction_in_transaction,
    ensure_tax_rule_in_transaction,
    service,
)
from dotmac_tax.models import (
    TaxAuthority,
    TaxCode,
    TaxJurisdiction,
    TaxRule,
    TaxRuleBand,
)
from sqlalchemy import event, func, select, text

from tests.unit import test_tax_service as helpers

db = helpers.db


def test_owner_ensure_apis_publish_existing_orm_free_contract_types():
    for function, dto in (
        (ensure_tax_authority_in_transaction, TaxAuthorityV1),
        (ensure_tax_jurisdiction_in_transaction, TaxJurisdictionV1),
        (ensure_tax_code_in_transaction, TaxCodeV1),
        (ensure_tax_rule_in_transaction, TaxRuleV1),
    ):
        assert function.__name__ in dotmac_tax.__all__
        assert function.__name__ in service.__all__
        assert get_type_hints(function)["return"] is dto


@contextmanager
def participant_guard(session):
    """Real Session events mirror Sub's registered owner completion checks."""
    nested = []
    rollbacks = []

    def before_commit(current):
        if current.info.get("tax_test_owner_active"):
            raise AssertionError("only the public owner boundary may commit")

    def after_transaction_create(_session, transaction):
        if transaction.nested:
            nested.append(transaction)

    def after_soft_rollback(_session, transaction):
        rollbacks.append(transaction)

    session.info["tax_test_owner_active"] = True
    event.listen(session, "before_commit", before_commit)
    event.listen(session, "after_transaction_create", after_transaction_create)
    event.listen(session, "after_soft_rollback", after_soft_rollback)
    try:
        yield
        assert not nested, "participant opened a savepoint"
        assert not rollbacks, "participant rolled back a transaction"
    finally:
        session.info.pop("tax_test_owner_active", None)
        event.remove(session, "before_commit", before_commit)
        event.remove(session, "after_transaction_create", after_transaction_create)
        event.remove(session, "after_soft_rollback", after_soft_rollback)


def author_catalog(session, tenant_id, *, suffix="1"):
    authority_input = TaxAuthorityInput(f" AUTH-{suffix} ", " Authority ", " national ")
    authority = ensure_tax_authority_in_transaction(
        session, tenant_id=tenant_id, command=authority_input
    )
    jurisdiction_input = TaxJurisdictionInput(
        authority.authority_id,
        f" JUR-{suffix} ",
        " Jurisdiction ",
        " ng ",
        helpers.NGN,
        " subdivision ",
    )
    jurisdiction = ensure_tax_jurisdiction_in_transaction(
        session, tenant_id=tenant_id, command=jurisdiction_input
    )
    code_input = {
        "jurisdiction_id": jurisdiction.jurisdiction_id,
        "code": f" CODE-{suffix} ",
        "name": " Levy ",
        "tax_kind_code": " configurable ",
        "description": " Policy ",
    }
    code = ensure_tax_code_in_transaction(session, tenant_id=tenant_id, **code_input)
    return (
        authority_input,
        authority,
        jurisdiction_input,
        jurisdiction,
        code_input,
        code,
    )


def rule_input(code_id):
    return TaxRuleInput(
        code_id,
        1,
        date(2026, 1, 1),
        None,
        10,
        " invoice ",
        " issued ",
        "output",
        "progressive",
        None,
        None,
        False,
        Decimal("0"),
        bands=(
            TaxRuleBandInput(1, Decimal("0"), Decimal("100"), Decimal("0.1")),
            TaxRuleBandInput(2, Decimal("100"), None, Decimal("0.2")),
        ),
    )


@pytest.mark.parametrize("autoflush", [True, False])
def test_first_create_and_exact_replay_return_dtos_without_participant_completion(
    db, autoflush
):
    tenant = helpers._tenant(db)
    db.autoflush = autoflush
    with participant_guard(db):
        (
            authority_input,
            authority,
            jurisdiction_input,
            jurisdiction,
            code_input,
            code,
        ) = author_catalog(db, tenant.id)
        rule_command = rule_input(code.tax_code_id)
        rule = ensure_tax_rule_in_transaction(
            db, tenant_id=tenant.id, command=rule_command
        )
        assert isinstance(authority, TaxAuthorityV1)
        assert isinstance(jurisdiction, TaxJurisdictionV1)
        assert isinstance(code, TaxCodeV1)
        assert isinstance(rule, TaxRuleV1)
        assert (
            ensure_tax_authority_in_transaction(
                db, tenant_id=tenant.id, command=authority_input
            )
            == authority
        )
        assert (
            ensure_tax_jurisdiction_in_transaction(
                db, tenant_id=tenant.id, command=jurisdiction_input
            )
            == jurisdiction
        )
        assert (
            ensure_tax_code_in_transaction(db, tenant_id=tenant.id, **code_input)
            == code
        )
        assert (
            ensure_tax_rule_in_transaction(
                db, tenant_id=tenant.id, command=rule_command
            )
            == rule
        )
        assert rule.bands[1].rate == Decimal("0.2")
        assert db.autoflush is autoflush
    for model in (TaxAuthority, TaxJurisdiction, TaxCode, TaxRule):
        assert db.scalar(select(func.count()).select_from(model)) == 1
    assert db.scalar(select(func.count()).select_from(TaxRuleBand)) == 2


@pytest.mark.parametrize("kind", ["authority", "jurisdiction", "code", "rule"])
def test_exact_replay_rejects_current_content_drift(db, kind):
    tenant = helpers._tenant(db)
    authority_input, _, jurisdiction_input, _, code_input, code = author_catalog(
        db, tenant.id
    )
    rule_command = rule_input(code.tax_code_id)
    ensure_tax_rule_in_transaction(db, tenant_id=tenant.id, command=rule_command)
    with participant_guard(db), pytest.raises(TaxConflict):
        if kind == "authority":
            ensure_tax_authority_in_transaction(
                db,
                tenant_id=tenant.id,
                command=replace(authority_input, name="Different"),
            )
        elif kind == "jurisdiction":
            ensure_tax_jurisdiction_in_transaction(
                db,
                tenant_id=tenant.id,
                command=replace(jurisdiction_input, country_code="US"),
            )
        elif kind == "code":
            ensure_tax_code_in_transaction(
                db, tenant_id=tenant.id, **{**code_input, "description": "Different"}
            )
        else:
            ensure_tax_rule_in_transaction(
                db, tenant_id=tenant.id, command=replace(rule_command, priority=11)
            )


@pytest.mark.parametrize("autoflush", [True, False])
def test_dirty_catalog_is_flushed_and_compared_without_overwriting_pending_work(
    db, autoflush
):
    tenant = helpers._tenant(db)
    command = TaxAuthorityInput("AUTH", "Original")
    authority = ensure_tax_authority_in_transaction(
        db, tenant_id=tenant.id, command=command
    )
    row = db.get(TaxAuthority, authority.authority_id)
    row.name = "Changed by owner"
    db.autoflush = autoflush
    with participant_guard(db), pytest.raises(TaxConflict):
        ensure_tax_authority_in_transaction(db, tenant_id=tenant.id, command=command)
    assert db.autoflush is autoflush
    assert (
        db.scalar(select(TaxAuthority.name).where(TaxAuthority.id == row.id))
        == "Changed by owner"
    )


def test_preloaded_catalog_refreshes_after_external_sql_change(db):
    tenant = helpers._tenant(db)
    command = TaxAuthorityInput("AUTH", "Original")
    authority = ensure_tax_authority_in_transaction(
        db, tenant_id=tenant.id, command=command
    )
    cached = db.get(TaxAuthority, authority.authority_id)
    db.execute(text("UPDATE tax_authorities SET name = 'Current' WHERE code = 'AUTH'"))
    assert cached.name == "Original"
    with participant_guard(db), pytest.raises(TaxConflict):
        ensure_tax_authority_in_transaction(db, tenant_id=tenant.id, command=command)
    assert cached.name == "Current"


def test_unpublished_rule_is_never_an_exact_replay(db):
    tenant = helpers._tenant(db)
    *_, code = author_catalog(db, tenant.id)
    command = rule_input(code.tax_code_id)
    rule = ensure_tax_rule_in_transaction(db, tenant_id=tenant.id, command=command)
    row = db.get(TaxRule, rule.rule_id)
    row.is_published = False  # SQLite canary; PostgreSQL forbids this mutation.
    with participant_guard(db), pytest.raises(TaxConflict, match="not published"):
        ensure_tax_rule_in_transaction(db, tenant_id=tenant.id, command=command)


def test_cross_tenant_parent_and_invalid_scope_are_refused(db):
    first, second = helpers._tenant(db), helpers._tenant(db)
    _, authority, jurisdiction_input, jurisdiction, code_input, code = author_catalog(
        db, first.id
    )
    with participant_guard(db):
        with pytest.raises(TaxNotFound):
            ensure_tax_jurisdiction_in_transaction(
                db, tenant_id=second.id, command=jurisdiction_input
            )
        with pytest.raises(TaxNotFound):
            ensure_tax_code_in_transaction(db, tenant_id=second.id, **code_input)
        with pytest.raises(TaxNotFound):
            ensure_tax_rule_in_transaction(
                db, tenant_id=second.id, command=rule_input(code.tax_code_id)
            )
        with pytest.raises(TypeError):
            ensure_tax_authority_in_transaction(
                db, tenant_id=None, command=TaxAuthorityInput("AUTH", "Invalid")
            )
    assert authority.tenant_id == jurisdiction.tenant_id == first.id


def test_session_event_guard_rejects_existing_internal_savepoint_completion(db):
    tenant = helpers._tenant(db)
    with (
        pytest.raises(AssertionError, match="public owner boundary"),
        participant_guard(db),
    ):
        ensure_tax_authority(
            db, tenant_id=tenant.id, command=TaxAuthorityInput("NEW", "New")
        )
    db.rollback()  # The test's caller owns rollback after rejected completion.
