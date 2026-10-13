"""Real migrated PostgreSQL canaries for bounded tenant policy reads."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.models import Tenant
from dotmac_kernel.money import Currency, Money
from dotmac_tax._policy_queries import (
    list_tax_authorities,
    list_tax_codes,
    list_tax_jurisdictions,
    list_tax_rule_history,
)
from dotmac_tax._policy_query_contracts import (
    ListTaxAuthoritiesV1,
    ListTaxCodesV1,
    ListTaxJurisdictionsV1,
    ListTaxRuleHistoryV1,
)
from dotmac_tax.contracts import (
    TaxAuthorityInput,
    TaxJurisdictionInput,
    TaxRuleInput,
    TaxRuleSupersessionInput,
)
from dotmac_tax.models import TaxAuthority, TaxRule
from dotmac_tax.service import (
    TaxNotFound,
    TaxRuleViolation,
    create_tax_authority,
    create_tax_code,
    create_tax_jurisdiction,
    publish_tax_rule,
    supersede_tax_rule,
)
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from tests import test_tax_released_migration_upgrade as tax_upgrade

database = tax_upgrade.database


@pytest.fixture
def migrated_tax(database, monkeypatch):
    from alembic import command

    _, admin_url, app_url = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    command.upgrade(tax_upgrade._config(tax_upgrade.CURRENT_TAX), "heads")
    return admin_url, app_url


@pytest.fixture
def admin_session(migrated_tax):
    engine = create_engine(migrated_tax[0])
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            yield db
    finally:
        engine.dispose()


def _seed(db, tenant_id):
    authority = create_tax_authority(
        db, tenant_id=tenant_id, command=TaxAuthorityInput("AUTH", "Authority")
    )
    jurisdiction = create_tax_jurisdiction(
        db,
        tenant_id=tenant_id,
        command=TaxJurisdictionInput(
            authority.id, "NG", "Nigeria", "NG", Currency("NGN", 2)
        ),
    )
    code = create_tax_code(
        db,
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code="LEVY",
        name="Composable levy",
        tax_kind_code="tenant-levy",
    )
    db.flush()
    return authority.id, jurisdiction.id, code.id


@pytest.fixture
def policy(admin_session):
    tenant_a = Tenant(id=uuid4(), slug="policy-alpha", name="Policy Alpha")
    tenant_b = Tenant(id=uuid4(), slug="policy-beta", name="Policy Beta")
    admin_session.add_all((tenant_a, tenant_b))
    admin_session.flush()
    first = _seed(admin_session, tenant_a.id)
    second = _seed(admin_session, tenant_b.id)
    admin_session.commit()
    return tenant_a.id, first, tenant_b.id, second


def test_owner_connection_reads_only_declared_tenant_and_rejects_foreign_filters(
    admin_session, policy
):
    tenant, first, _, foreign = policy
    authorities = list_tax_authorities(
        admin_session, tenant_id=tenant, request=ListTaxAuthoritiesV1()
    )
    assert tuple(item.authority_id for item in authorities.items) == (first[0],)
    assert all(item.tenant_id == tenant for item in authorities.items)
    jurisdictions = list_tax_jurisdictions(
        admin_session,
        tenant_id=tenant,
        request=ListTaxJurisdictionsV1(
            authority_id=first[0], country_code="NG", status="active"
        ),
    )
    assert tuple(item.jurisdiction_id for item in jurisdictions.items) == (first[1],)
    assert jurisdictions.items[0].currency == Currency("NGN", 2)
    codes = list_tax_codes(
        admin_session,
        tenant_id=tenant,
        request=ListTaxCodesV1(jurisdiction_id=first[1], tax_kind_code="tenant-levy"),
    )
    assert tuple(item.tax_code_id for item in codes.items) == (first[2],)
    with pytest.raises(TaxNotFound):
        list_tax_jurisdictions(
            admin_session,
            tenant_id=tenant,
            request=ListTaxJurisdictionsV1(authority_id=foreign[0]),
        )
    with pytest.raises(TaxNotFound):
        list_tax_codes(
            admin_session,
            tenant_id=tenant,
            request=ListTaxCodesV1(jurisdiction_id=foreign[1]),
        )
    with pytest.raises(TaxNotFound):
        list_tax_rule_history(
            admin_session,
            tenant_id=tenant,
            request=ListTaxRuleHistoryV1(tax_code_id=foreign[2]),
        )


def test_app_user_rls_cannot_read_another_declared_tenant(migrated_tax, policy):
    tenant, _, foreign_tenant, _ = policy
    engine = create_engine(migrated_tax[1])
    try:
        with Session(engine) as db:
            role = db.scalar(text("SELECT current_user"))
            assert role == "app_user", "RLS proof must use app_user, never owner"
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant)},
            )
            assert (
                list_tax_authorities(
                    db, tenant_id=foreign_tenant, request=ListTaxAuthoritiesV1()
                ).items
                == ()
            )
            assert list_tax_authorities(
                db, tenant_id=tenant, request=ListTaxAuthoritiesV1()
            ).items
    finally:
        engine.dispose()


def test_queries_are_frozen_scalar_reads_without_autoflush(admin_session, policy):
    tenant, first, _, _ = policy
    row = admin_session.scalar(select(TaxAuthority).where(TaxAuthority.id == first[0]))
    row.name = "Pending dirty change"
    admin_session.add(TaxAuthority(tenant_id=tenant, code="UNFLUSHED"))
    flushes = []

    def observed_flush(*args):
        flushes.append(True)

    event.listen(admin_session, "before_flush", observed_flush)
    try:
        with admin_session.no_autoflush:
            admin_session.autoflush = True
            page = list_tax_authorities(
                admin_session, tenant_id=tenant, request=ListTaxAuthoritiesV1()
            )
            list_tax_jurisdictions(
                admin_session,
                tenant_id=tenant,
                request=ListTaxJurisdictionsV1(authority_id=first[0]),
            )
            list_tax_codes(
                admin_session,
                tenant_id=tenant,
                request=ListTaxCodesV1(jurisdiction_id=first[1]),
            )
            list_tax_rule_history(
                admin_session,
                tenant_id=tenant,
                request=ListTaxRuleHistoryV1(tax_code_id=first[2]),
            )
        assert flushes == []
        assert page.items[0].name == "Authority"
        assert isinstance(page.items, tuple)
        with pytest.raises(FrozenInstanceError):
            page.items[0].name = "Mutated"
        with pytest.raises(FrozenInstanceError):
            page.next_cursor = "changed"
    finally:
        event.remove(admin_session, "before_flush", observed_flush)
        admin_session.autoflush = False
        admin_session.rollback()


def test_keyset_pages_survive_changes_before_cursor_and_bind_scope(
    admin_session, policy
):
    tenant, _, other_tenant, _ = policy
    admin_session.add_all(
        TaxAuthority(
            id=UUID(int=n),
            tenant_id=tenant,
            code=f"PAGE-{n}",
            name="Page",
            status="active",
        )
        for n in (10, 20, 30)
    )
    admin_session.flush()
    request = ListTaxAuthoritiesV1(page_size=2)
    first = list_tax_authorities(admin_session, tenant_id=tenant, request=request)
    assert [item.authority_id for item in first.items] == [UUID(int=10), UUID(int=20)]
    assert first.next_cursor
    admin_session.add(
        TaxAuthority(
            id=UUID(int=15),
            tenant_id=tenant,
            code="INSERTED",
            name="Earlier",
            status="active",
        )
    )
    admin_session.flush()
    second = list_tax_authorities(
        admin_session,
        tenant_id=tenant,
        request=replace(request, cursor=first.next_cursor),
    )
    assert second.items[0].authority_id == UUID(int=30)
    assert not (
        {x.authority_id for x in first.items} & {x.authority_id for x in second.items}
    )
    assert second.next_cursor is None
    for changed in (replace(request, status="retired"), request):
        declared_tenant = tenant if changed.status else other_tenant
        with pytest.raises(TaxRuleViolation, match="cursor"):
            list_tax_authorities(
                admin_session,
                tenant_id=declared_tenant,
                request=replace(changed, cursor=first.next_cursor),
            )
    with pytest.raises(TaxRuleViolation, match="cursor"):
        list_tax_authorities(
            admin_session, tenant_id=tenant, request=replace(request, cursor="broken")
        )


@pytest.mark.parametrize("size", [0, -1, 101, True, 1.5])
def test_invalid_page_size_is_rejected(size):
    with pytest.raises(ValueError):
        ListTaxAuthoritiesV1(page_size=size)


def test_rule_history_preserves_exact_versions_and_filters(admin_session, policy):
    tenant, first, _, _ = policy
    command = TaxRuleInput(
        tax_code_id=first[2],
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 6, 30),
        priority=10,
        fact_kind="service",
        recognition_basis_code="issued",
        transaction_side="output",
        calculation_method="percentage",
        rate=Decimal("0.07123456"),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal("0"),
    )
    publish_tax_rule(admin_session, tenant_id=tenant, command=command)
    publish_tax_rule(
        admin_session,
        tenant_id=tenant,
        command=replace(
            command, version=2, effective_from=date(2026, 7, 1), effective_to=None
        ),
    )
    request = ListTaxRuleHistoryV1(tax_code_id=first[2], page_size=1)
    first_page = list_tax_rule_history(admin_session, tenant_id=tenant, request=request)
    assert first_page.items[0].rule.version == 1
    assert first_page.items[0].rule.rate == command.rate
    assert first_page.items[0].rule.published_at.utcoffset().total_seconds() == 0
    assert first_page.items[0].rule.effective_to == command.effective_to
    second = list_tax_rule_history(
        admin_session,
        tenant_id=tenant,
        request=replace(request, cursor=first_page.next_cursor),
    )
    assert [item.rule.version for item in second.items] == [2]
    assert second.next_cursor is None
    assert (
        list_tax_codes(
            admin_session,
            tenant_id=tenant,
            request=ListTaxCodesV1(jurisdiction_id=first[1], tax_kind_code="unmatched"),
        ).items
        == ()
    )
    with pytest.raises(TaxNotFound):
        list_tax_codes(
            admin_session,
            tenant_id=tenant,
            request=ListTaxCodesV1(jurisdiction_id=uuid4()),
        )


def test_rule_history_exposes_supersession_provenance_without_rewriting_original(
    admin_session, policy
):
    tenant, first, _, _ = policy
    command = TaxRuleInput(
        tax_code_id=first[2],
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=10,
        fact_kind="service",
        recognition_basis_code="issued",
        transaction_side="output",
        calculation_method="fixed",
        rate=None,
        fixed_amount=Money(Decimal("12.34"), Currency("NGN", 2)),
        inclusive=False,
        recoverable_rate=Decimal("0"),
    )
    original = publish_tax_rule(admin_session, tenant_id=tenant, command=command)
    actor = uuid4()
    replacement = supersede_tax_rule(
        admin_session,
        tenant_id=tenant,
        command=TaxRuleSupersessionInput(
            predecessor_rule_id=original.id,
            predecessor_version=1,
            replacement=replace(command, version=2, effective_from=date(2026, 7, 1)),
            effective_from=date(2026, 7, 1),
            actor_id=actor,
            published_by_ref="policy-author",
            reason="Updated configured policy",
            evidence_ref="approval:policy-2",
            source_ref="policy-form:2",
            source_version="1",
        ),
    )
    page = list_tax_rule_history(
        admin_session,
        tenant_id=tenant,
        request=ListTaxRuleHistoryV1(tax_code_id=first[2]),
    )
    assert [item.rule.version for item in page.items] == [1, 2]
    assert page.items[0].rule.effective_to is None
    assert page.items[0].rule.fixed_amount == command.fixed_amount
    edge = page.items[0].supersession
    assert edge is not None
    assert edge.replacement_rule_id == replacement.replacement.rule_id
    assert edge.actor_id == actor
    assert edge.reason == "Updated configured policy"
    assert edge.evidence_ref == "approval:policy-2"
    assert edge.source_ref == "policy-form:2"
    assert edge.source_version == "1"
    assert edge.effective_from == date(2026, 7, 1)
    assert edge.published_at == replacement.published_at
    assert page.items[1].supersession is None


def test_rule_history_hides_flushed_unpublished_construction(admin_session, policy):
    tenant, first, _, _ = policy
    construction = TaxRule(
        id=uuid4(),
        tenant_id=tenant,
        tax_code_id=first[2],
        version=1,
        is_published=False,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=10,
        fact_kind="service",
        recognition_basis_code="issued",
        transaction_side="output",
        calculation_method="percentage",
        rate=Decimal("0.07123456"),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal("0"),
        treatment_code="standard_rated",
        calculation_sequence=100,
        calculation_base_code="source_amount",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    try:
        # Construction may exist only within this transaction. The deferred
        # publication constraint forbids committing it; cleanup always rolls back.
        admin_session.add(construction)
        admin_session.flush()
        assert (
            admin_session.scalar(
                select(TaxRule.id).where(
                    TaxRule.tenant_id == tenant,
                    TaxRule.id == construction.id,
                    TaxRule.is_published.is_(False),
                )
            )
            == construction.id
        )
        page = list_tax_rule_history(
            admin_session,
            tenant_id=tenant,
            request=ListTaxRuleHistoryV1(tax_code_id=first[2]),
        )
        assert page.items == ()
        assert page.next_cursor is None
    finally:
        admin_session.rollback()
    assert (
        admin_session.scalar(
            select(TaxRule.id).where(
                TaxRule.tenant_id == tenant, TaxRule.id == construction.id
            )
        )
        is None
    )
