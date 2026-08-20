"""Sub reseller parity at the new tenant-only owner boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Base, Tenant
from dotmac_reseller_management import (
    BindCustomerAccount,
    BindMember,
    ChangeStatus,
    ContractError,
    CreateResellerAccount,
    PublishAuthority,
    SetParent,
    bind_customer_account,
    bind_member,
    create_account,
    publish_authority,
    set_parent,
    transition_account,
)
from dotmac_reseller_management.models import ALL_MODELS, ResellerAccount
from dotmac_reseller_management.service import Conflict, InvalidTransition
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"public": None, "mod_reseller": None}
        },
    )
    Base.metadata.create_all(
        engine,
        tables=(
            Tenant.__table__,
            OutboxEvent.__table__,
            *(model.__table__ for model in ALL_MODELS),
        ),
    )
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


def _account(
    db: Session,
    *,
    scope: TenantScope,
    code: str,
    parent_account_id: uuid.UUID | None = None,
) -> ResellerAccount:
    return create_account(
        db,
        scope=scope,
        command=CreateResellerAccount(
            code=code,
            name=code.title(),
            party_role_ref=f"party-role:{code.lower()}",
            parent_account_id=parent_account_id,
        ),
        recorded_at=NOW,
    )


def test_child_authority_is_an_immutable_subset_of_parent_authority(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    parent = _account(db, scope=scope, code="MASTER")
    parent_revision = publish_authority(
        db,
        scope=scope,
        command=PublishAuthority(
            account_id=parent.id,
            authority_codes=("customer.create", "customer.read"),
            evidence_ref="approval:master-v1",
        ),
        recorded_at=NOW,
    )
    child = _account(db, scope=scope, code="CHILD", parent_account_id=parent.id)

    with pytest.raises(Conflict, match="parent authority"):
        publish_authority(
            db,
            scope=scope,
            command=PublishAuthority(
                account_id=child.id,
                authority_codes=("billing.payout", "customer.read"),
                evidence_ref="approval:child-invalid",
            ),
            recorded_at=NOW,
        )

    first = publish_authority(
        db,
        scope=scope,
        command=PublishAuthority(
            account_id=child.id,
            authority_codes=("customer.read",),
            evidence_ref="approval:child-v1",
        ),
        recorded_at=NOW,
    )
    second = publish_authority(
        db,
        scope=scope,
        command=PublishAuthority(
            account_id=child.id,
            authority_codes=(),
            evidence_ref="approval:child-v2",
        ),
        recorded_at=NOW,
    )

    assert parent_revision.authority_codes == ["customer.create", "customer.read"]
    assert first.version_number == 1
    assert second.version_number == 2
    assert first.id != second.id


def test_hierarchy_rejects_cycles_and_cross_tenant_parents(db: Session) -> None:
    alpha = TenantScope(TENANT_A)
    bravo = TenantScope(TENANT_B)
    root = _account(db, scope=alpha, code="ROOT")
    child = _account(db, scope=alpha, code="CHILD", parent_account_id=root.id)
    foreign = _account(db, scope=bravo, code="FOREIGN")

    with pytest.raises(Conflict, match="cycle"):
        set_parent(
            db,
            scope=alpha,
            command=SetParent(
                account_id=root.id,
                parent_account_id=child.id,
                evidence_ref="case:cycle",
            ),
            recorded_at=NOW,
        )
    with pytest.raises(Conflict, match="same tenant"):
        set_parent(
            db,
            scope=alpha,
            command=SetParent(
                account_id=root.id,
                parent_account_id=foreign.id,
                evidence_ref="case:cross-tenant",
            ),
            recorded_at=NOW,
        )


def test_bindings_are_idempotent_and_do_not_own_collaborator_rows(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    account = _account(db, scope=scope, code="BIND")
    member = BindMember(
        account_id=account.id,
        member_ref="party-person:42",
        evidence_ref="membership:42",
    )
    customer = BindCustomerAccount(
        account_id=account.id,
        customer_account_ref="subscriber:99",
        evidence_ref="assignment:99",
    )

    assert (
        bind_member(db, scope=scope, command=member, recorded_at=NOW).id
        == bind_member(db, scope=scope, command=member, recorded_at=NOW).id
    )
    assert (
        bind_customer_account(db, scope=scope, command=customer, recorded_at=NOW).id
        == bind_customer_account(db, scope=scope, command=customer, recorded_at=NOW).id
    )
    assert not hasattr(account, "commission_rate")
    assert not hasattr(account, "payout_account")
    assert not hasattr(account, "customer_status")


def test_retired_is_terminal_and_transition_emits_provider_neutral_fact(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    account = _account(db, scope=scope, code="LIFECYCLE")
    transition_account(
        db,
        scope=scope,
        command=ChangeStatus(
            account_id=account.id,
            target_status="suspended",
            evidence_ref="case:suspend",
        ),
        recorded_at=NOW,
    )
    transition_account(
        db,
        scope=scope,
        command=ChangeStatus(
            account_id=account.id,
            target_status="retired",
            evidence_ref="case:retire",
        ),
        recorded_at=NOW,
    )
    with pytest.raises(InvalidTransition, match="terminal"):
        transition_account(
            db,
            scope=scope,
            command=ChangeStatus(
                account_id=account.id,
                target_status="active",
                evidence_ref="case:restore",
            ),
            recorded_at=NOW,
        )

    events = db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at)).all()
    assert [event.event_type for event in events] == [
        "reseller.account.status-changed.v1",
        "reseller.account.status-changed.v1",
    ]
    assert events[-1].payload["evidence_ref"] == "case:retire"


def test_contract_refuses_ambient_identity_and_unknown_status() -> None:
    with pytest.raises(ContractError, match="party_role_ref"):
        CreateResellerAccount(code="A", name="Alpha", party_role_ref="  ")
    with pytest.raises(ContractError, match="target_status"):
        ChangeStatus(
            account_id=uuid.uuid4(),
            target_status="deleted",
            evidence_ref="case:1",
        )
