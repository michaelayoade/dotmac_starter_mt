"""Service-order readiness behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_orders import models
from dotmac_service_orders.contracts import (
    ConfirmActivation,
    Conflict,
    DecideReadiness,
    OpenServiceOrder,
    ReadinessCheck,
    ReadinessCheckKind,
    ReadinessCheckResult,
    ReadinessDecisionStatus,
    ServiceOrderStatus,
    ServiceOrderType,
)
from dotmac_service_orders.models import TENANT_TABLES, ReadinessEvidenceImmutableError
from dotmac_service_orders.service import (
    begin_delivery,
    confirm_activation,
    decide_readiness,
    latest_readiness,
    open_service_order,
    submit_service_order,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 22, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_serviceorders": None}},
    )
    Tenant.__table__.create(engine)
    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="a", name="A"),
                Tenant(id=TENANT_B, slug="b", name="B"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _in_delivery(db: Session, scope: TenantScope, key: str = "req-1"):
    order = open_service_order(
        db,
        scope=scope,
        command=OpenServiceOrder(
            customer_reference="customer:1",
            order_type=ServiceOrderType.NEW_INSTALL,
            request_key=key,
        ),
    )
    submit_service_order(db, scope=scope, service_order_id=order.id)
    return begin_delivery(db, scope=scope, service_order_id=order.id)


def _check(
    kind: ReadinessCheckKind,
    result: ReadinessCheckResult = ReadinessCheckResult.PASSED,
    reason: str = "ok",
) -> ReadinessCheck:
    return ReadinessCheck(kind, result, reason, "observation")


def test_opening_is_idempotent_on_the_request_key(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    command = OpenServiceOrder(
        customer_reference="customer:1",
        order_type=ServiceOrderType.NEW_INSTALL,
        request_key="req-1",
    )
    first = open_service_order(db, scope=scope, command=command)
    second = open_service_order(db, scope=scope, command=command)
    assert first.id == second.id

    with pytest.raises(Conflict):
        open_service_order(
            db,
            scope=scope,
            command=OpenServiceOrder(
                customer_reference="customer:2",
                order_type=ServiceOrderType.NEW_INSTALL,
                request_key="req-1",
            ),
        )


def test_all_checks_passing_requests_activation(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    order = _in_delivery(db, scope)
    decision = decide_readiness(
        db,
        scope=scope,
        command=DecideReadiness(
            service_order_id=order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            checks=(
                _check(ReadinessCheckKind.DELIVERY_RUN),
                _check(
                    ReadinessCheckKind.FIELD_WORK,
                    ReadinessCheckResult.NOT_APPLICABLE,
                    "field_work_not_required",
                ),
            ),
            decided_at=NOW,
        ),
    )
    assert decision.status is ReadinessDecisionStatus.ACTIVATION_REQUESTED
    assert order.status is ServiceOrderStatus.IN_DELIVERY


def test_a_failed_delivery_run_is_terminal_but_another_failure_only_blocks(
    db: Session,
) -> None:
    """Sub's exact split: a failed run fails the order, anything else is
    retryable. Collapsing them either strands recoverable orders or hides a
    failed provisioning run behind a blocked-looking state."""
    scope = TenantScope(TENANT_A)
    blocked_order = _in_delivery(db, scope, key="req-block")
    blocked = decide_readiness(
        db,
        scope=scope,
        command=DecideReadiness(
            service_order_id=blocked_order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            checks=(
                _check(ReadinessCheckKind.DELIVERY_RUN),
                _check(
                    ReadinessCheckKind.ACTIVATION_TASK,
                    ReadinessCheckResult.FAILED,
                    "activation_task_incomplete",
                ),
            ),
            decided_at=NOW,
        ),
    )
    assert blocked.status is ReadinessDecisionStatus.BLOCKED
    assert blocked.reason_code == "activation_task_incomplete"
    assert blocked_order.status is ServiceOrderStatus.IN_DELIVERY

    failed_order = _in_delivery(db, scope, key="req-fail")
    failed = decide_readiness(
        db,
        scope=scope,
        command=DecideReadiness(
            service_order_id=failed_order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            checks=(
                _check(
                    ReadinessCheckKind.DELIVERY_RUN,
                    ReadinessCheckResult.FAILED,
                    "delivery_run_failed",
                ),
            ),
            decided_at=NOW,
        ),
    )
    assert failed.status is ReadinessDecisionStatus.FAILED
    assert failed_order.status is ServiceOrderStatus.FAILED


def test_a_replayed_command_returns_its_decision_but_refuses_another_order(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    order = _in_delivery(db, scope, key="req-a")
    other = _in_delivery(db, scope, key="req-b")
    command_id = uuid.uuid4()
    command = DecideReadiness(
        service_order_id=order.id,
        command_id=command_id,
        correlation_id=uuid.uuid4(),
        actor="operator:1",
        checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
        decided_at=NOW,
    )
    first = decide_readiness(db, scope=scope, command=command)
    assert decide_readiness(db, scope=scope, command=command).id == first.id

    with pytest.raises(Conflict):
        decide_readiness(
            db,
            scope=scope,
            command=DecideReadiness(
                service_order_id=other.id,
                command_id=command_id,
                correlation_id=uuid.uuid4(),
                actor="operator:1",
                checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
                decided_at=NOW,
            ),
        )


def test_activation_nobody_requested_cannot_be_confirmed(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    order = _in_delivery(db, scope)
    with pytest.raises(Conflict):
        confirm_activation(
            db,
            scope=scope,
            command=ConfirmActivation(
                service_order_id=order.id,
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                actor="operator:1",
            ),
        )

    decide_readiness(
        db,
        scope=scope,
        command=DecideReadiness(
            service_order_id=order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
            decided_at=NOW,
        ),
    )
    confirmed = confirm_activation(
        db,
        scope=scope,
        command=ConfirmActivation(
            service_order_id=order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            decided_at=NOW,
        ),
    )
    assert confirmed.status is ReadinessDecisionStatus.ACTIVATED
    assert order.status is ServiceOrderStatus.ACTIVATED
    assert latest_readiness(db, scope=scope, service_order_id=order.id) is not None


def test_a_decision_needs_checks_and_at_most_one_per_kind(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    order = _in_delivery(db, scope)
    with pytest.raises(Conflict):
        decide_readiness(
            db,
            scope=scope,
            command=DecideReadiness(
                service_order_id=order.id,
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                actor="operator:1",
                checks=(),
            ),
        )
    with pytest.raises(Conflict):
        decide_readiness(
            db,
            scope=scope,
            command=DecideReadiness(
                service_order_id=order.id,
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                actor="operator:1",
                checks=(
                    _check(ReadinessCheckKind.DELIVERY_RUN),
                    _check(ReadinessCheckKind.DELIVERY_RUN, reason="again"),
                ),
            ),
        )


def test_readiness_evidence_cannot_be_rewritten(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    order = _in_delivery(db, scope)
    decision = decide_readiness(
        db,
        scope=scope,
        command=DecideReadiness(
            service_order_id=order.id,
            command_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            actor="operator:1",
            checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
            decided_at=NOW,
        ),
    )
    decision.reason_code = "rewritten"
    with pytest.raises(ReadinessEvidenceImmutableError):
        db.flush()
    db.expunge_all()


def test_a_draft_order_cannot_be_decided(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    order = open_service_order(
        db,
        scope=scope,
        command=OpenServiceOrder(
            customer_reference="customer:1",
            order_type=ServiceOrderType.NEW_INSTALL,
            request_key="req-draft",
        ),
    )
    with pytest.raises(Conflict):
        decide_readiness(
            db,
            scope=scope,
            command=DecideReadiness(
                service_order_id=order.id,
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                actor="operator:1",
                checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
            ),
        )


def test_another_tenants_order_is_not_visible(db: Session) -> None:
    order = _in_delivery(db, TenantScope(TENANT_A))
    with pytest.raises(Conflict):
        decide_readiness(
            db,
            scope=TenantScope(TENANT_B),
            command=DecideReadiness(
                service_order_id=order.id,
                command_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                actor="operator:2",
                checks=(_check(ReadinessCheckKind.DELIVERY_RUN),),
            ),
        )
