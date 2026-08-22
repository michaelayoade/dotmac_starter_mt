"""Behavior canaries ported from Sub's customer-context boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from dotmac_customers.contracts import (
    AccountStatus,
    Conflict,
    CreateCustomerAccount,
    LinkPartyReference,
    NotFound,
    PartyReferenceRole,
    SetCustomerProfile,
)
from dotmac_customers.models import TENANT_TABLES
from dotmac_customers.service import (
    create_account,
    get_account,
    link_party_reference,
    set_profile,
    transition_account,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_customers": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_customers import models

    for table_name in TENANT_TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def test_account_number_is_normalized_and_unique_per_tenant(db: Session) -> None:
    command = CreateCustomerAccount(account_number=" cust-001 ", display_name="Ada")
    account = create_account(db, scope=TenantScope(TENANT_A), command=command)
    assert account.account_number == "CUST-001"
    assert account.status == AccountStatus.PROSPECT
    with pytest.raises(Conflict, match="account number"):
        create_account(db, scope=TenantScope(TENANT_A), command=command)
    other = create_account(db, scope=TenantScope(TENANT_B), command=command)
    assert other.account_number == account.account_number


def test_profile_and_party_reference_belong_to_the_same_customer_tenant(
    db: Session,
) -> None:
    account = create_account(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateCustomerAccount("CUST-002", "Lovelace Networks"),
    )
    profile = set_profile(
        db,
        scope=TenantScope(TENANT_A),
        command=SetCustomerProfile(account.id, segment="enterprise", notes="VIP"),
    )
    reference = link_party_reference(
        db,
        scope=TenantScope(TENANT_A),
        command=LinkPartyReference(
            account.id,
            party_system="dotmac-party",
            party_reference="party-42",
            role=PartyReferenceRole.ACCOUNT_HOLDER,
        ),
    )
    assert profile.segment == "ENTERPRISE"
    assert reference.party_system == "dotmac-party"
    with pytest.raises(NotFound):
        get_account(db, scope=TenantScope(TENANT_B), account_id=account.id)


def test_closed_account_cannot_be_reactivated(db: Session) -> None:
    account = create_account(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateCustomerAccount("CUST-003", "Closed Customer"),
    )
    transition_account(
        db,
        scope=TenantScope(TENANT_A),
        account_id=account.id,
        target=AccountStatus.ACTIVE,
    )
    transition_account(
        db,
        scope=TenantScope(TENANT_A),
        account_id=account.id,
        target=AccountStatus.CLOSED,
    )
    with pytest.raises(Conflict, match="terminal"):
        transition_account(
            db,
            scope=TenantScope(TENANT_A),
            account_id=account.id,
            target=AccountStatus.ACTIVE,
        )


def test_mutations_are_flush_only(db: Session) -> None:
    create_account(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateCustomerAccount("CUST-004", "Rollback Customer"),
    )
    db.rollback()
    assert (
        get_account(
            db,
            scope=TenantScope(TENANT_A),
            account_number="CUST-004",
            required=False,
        )
        is None
    )
