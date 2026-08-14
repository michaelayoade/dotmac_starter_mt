"""Lifecycle on both planes, against real rows (ADR-0026).

SQLite here, so this suite proves LIFECYCLE and CONSTRAINTS, never tenancy —
row-level security cannot be exercised without Postgres, and
`tests/test_approvals_isolation.py` is where that is proven.

The two planes are driven side by side on purpose. They share their rules and
nothing else, so a change that fixes one and forgets the other should fail here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_approvals.contracts import (
    Actor,
    ApprovalLevel,
    ApprovalState,
    ApproverKind,
    ContentChanged,
    DecisionAction,
    NotRequester,
    PolicyNotFound,
    PolicyRevision,
    PolicyVersionExists,
    RequestNotPending,
    SoDRule,
)
from dotmac_approvals.models import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    PlatformApprovalDecision,
    PlatformApprovalPolicy,
    PlatformApprovalRequest,
)
from dotmac_approvals.service import (
    cancel_platform_request,
    cancel_tenant_request,
    evaluate_platform_approval,
    evaluate_tenant_approval,
    policy_document_digest,
    publish_platform_policy_version,
    publish_tenant_policy_version,
    record_platform_decision,
    record_tenant_decision,
    request_platform_approval,
    request_tenant_approval,
)
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
REQUESTER = uuid.uuid4()
ALICE = uuid.uuid4()
BOB = uuid.uuid4()
ROLE = uuid.uuid4()

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_approvals": None}},
    )
    for model in (
        ApprovalPolicy,
        ApprovalRequest,
        ApprovalDecision,
        PlatformApprovalPolicy,
        PlatformApprovalRequest,
        PlatformApprovalDecision,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _revision(*, quorum: int = 1, levels: int = 1, **kwargs: object) -> PolicyRevision:
    return PolicyRevision(
        policy_code="payment.release",
        version=1,
        levels=tuple(
            ApprovalLevel(
                sequence=index + 1,
                approver_kind=ApproverKind.ROLE,
                approver_id=str(ROLE),
                quorum=quorum,
            )
            for index in range(levels)
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _actor(actor_id: uuid.UUID) -> Actor:
    return Actor(actor_id=actor_id, role_ids=frozenset({ROLE}))


def _open_tenant(db: Session, *, digest: str = DIGEST, key: str = "k1") -> uuid.UUID:
    return request_tenant_approval(
        db,
        tenant_id=TENANT,
        policy_code="payment.release",
        policy_version=1,
        subject_type="finance.payment",
        subject_id=str(uuid.uuid4()),
        content_digest=digest,
        requested_by=REQUESTER,
        idempotency_key=key,
    ).request_id


# ── Fail closed ─────────────────────────────────────────────────────────────


def test_a_missing_policy_is_unavailable_never_implicitly_approved(
    db: Session,
) -> None:
    """ERP's `check_workflow_required` returned None when nothing matched, which
    a caller could read as "no approval needed". This refuses instead."""
    with pytest.raises(PolicyNotFound):
        _open_tenant(db)
    with pytest.raises(PolicyNotFound):
        request_platform_approval(
            db,
            policy_code="fleet.plan",
            policy_version=1,
            subject_type="fleet.plan",
            subject_id="plan-1",
            content_digest=DIGEST,
            requested_by=REQUESTER,
            idempotency_key="p1",
        )


def test_a_missing_VERSION_is_as_closed_as_a_missing_code(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    with pytest.raises(PolicyNotFound, match="v2"):
        request_tenant_approval(
            db,
            tenant_id=TENANT,
            policy_code="payment.release",
            policy_version=2,
            subject_type="finance.payment",
            subject_id="p",
            content_digest=DIGEST,
            requested_by=REQUESTER,
            idempotency_key="k",
        )


# ── Immutable revisions ─────────────────────────────────────────────────────


def test_a_published_policy_version_cannot_be_republished(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    with pytest.raises(PolicyVersionExists):
        publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())

    publish_platform_policy_version(db, revision=_revision())
    with pytest.raises(PolicyVersionExists):
        publish_platform_policy_version(db, revision=_revision())


def test_a_policy_document_digest_is_stable_and_content_addressed(
    db: Session,
) -> None:
    """The digest is what makes a cutover able to prove an imported revision is
    the one it replaced, rather than merely similarly named."""
    first = policy_document_digest(_revision())
    assert first == policy_document_digest(_revision())
    assert first != policy_document_digest(_revision(quorum=2))


def test_a_later_policy_version_does_not_reinterpret_an_open_request(
    db: Session,
) -> None:
    """ERP's mutable workflow row let an edit change what an in-flight request
    required. Here v2 exists alongside v1 and the request keeps its own."""
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    publish_tenant_policy_version(
        db,
        tenant_id=TENANT,
        revision=PolicyRevision(
            policy_code="payment.release",
            version=2,
            levels=(
                ApprovalLevel(
                    sequence=1,
                    approver_kind=ApproverKind.ROLE,
                    approver_id=str(ROLE),
                    quorum=5,
                ),
            ),
        ),
    )
    outcome = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert outcome.state is ApprovalState.APPROVED


# ── Content binding ─────────────────────────────────────────────────────────


def test_a_changed_digest_invalidates_the_decision(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    with pytest.raises(ContentChanged, match="the content changed"):
        record_tenant_decision(
            db,
            tenant_id=TENANT,
            request_id=request_id,
            actor=_actor(ALICE),
            action=DecisionAction.APPROVE,
            content_digest=OTHER_DIGEST,
        )
    assert (
        evaluate_tenant_approval(db, tenant_id=TENANT, request_id=request_id).state
        is ApprovalState.PENDING
    )


# ── Idempotency (Vendor CP delta; ERP had none) ─────────────────────────────


def test_the_same_key_and_content_returns_the_same_request(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    subject = str(uuid.uuid4())
    first = request_tenant_approval(
        db,
        tenant_id=TENANT,
        policy_code="payment.release",
        policy_version=1,
        subject_type="finance.payment",
        subject_id=subject,
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="retry-me",
    )
    second = request_tenant_approval(
        db,
        tenant_id=TENANT,
        policy_code="payment.release",
        policy_version=1,
        subject_type="finance.payment",
        subject_id=subject,
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="retry-me",
    )
    assert first.request_id == second.request_id
    assert second.events == ()  # a retry is not a second "requested" event
    assert len(db.execute(select(ApprovalRequest)).scalars().all()) == 1


def test_the_same_key_with_different_content_is_a_conflict(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    _open_tenant(db, key="shared")
    with pytest.raises(ContentChanged, match="different"):
        _open_tenant(db, digest=OTHER_DIGEST, key="shared")


# ── Quorum, concurrency and the durable half ────────────────────────────────


def test_two_actors_satisfying_the_final_quorum_produce_one_transition(
    db: Session,
) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision(quorum=2))
    request_id = _open_tenant(db)

    first = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert first.state is ApprovalState.PENDING
    assert first.events == ()

    second = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(BOB),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert second.state is ApprovalState.APPROVED
    assert [event.event_type for event in second.events] == ["approval.approved"]


def test_one_actor_voting_twice_is_impossible_at_the_database(db: Session) -> None:
    """The service refuses politely; this is the constraint that holds when two
    approvals race past the in-memory check."""
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision(quorum=2))
    request_id = _open_tenant(db)
    record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    db.add(
        ApprovalDecision(
            tenant_id=TENANT,
            request_id=request_id,
            level=1,
            actor_id=ALICE,
            action="approve",
            mfa_verified=False,
            decided_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ── Terminal states ─────────────────────────────────────────────────────────


def test_rejection_is_terminal_and_carries_its_reason(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    outcome = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.REJECT,
        content_digest=DIGEST,
        comment="wrong bank account",
    )
    assert outcome.state is ApprovalState.REJECTED
    assert [event.event_type for event in outcome.events] == ["approval.rejected"]
    with pytest.raises(RequestNotPending):
        record_tenant_decision(
            db,
            tenant_id=TENANT,
            request_id=request_id,
            actor=_actor(BOB),
            action=DecisionAction.APPROVE,
            content_digest=DIGEST,
        )


def test_only_the_requester_may_cancel(db: Session) -> None:
    """Ported from ERP, which enforced exactly this."""
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    with pytest.raises(NotRequester):
        cancel_tenant_request(
            db,
            tenant_id=TENANT,
            request_id=request_id,
            cancelled_by=ALICE,
            reason="not mine",
        )
    outcome = cancel_tenant_request(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        cancelled_by=REQUESTER,
        reason="superseded",
    )
    assert outcome.state is ApprovalState.CANCELLED
    assert [event.event_type for event in outcome.events] == ["approval.cancelled"]


# ── Ordered levels end to end ───────────────────────────────────────────────


def test_a_two_level_policy_advances_then_completes(db: Session) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision(levels=2))
    request_id = _open_tenant(db)
    first = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert first.state is ApprovalState.PENDING
    assert first.evaluation.current_level == 2

    second = record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(BOB),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert second.state is ApprovalState.APPROVED


def test_sod_across_levels_is_enforced_against_persisted_history(
    db: Session,
) -> None:
    publish_tenant_policy_version(
        db,
        tenant_id=TENANT,
        revision=PolicyRevision(
            policy_code="payment.release",
            version=1,
            levels=(
                ApprovalLevel(
                    sequence=1, approver_kind=ApproverKind.ROLE, approver_id=str(ROLE)
                ),
                ApprovalLevel(
                    sequence=2,
                    approver_kind=ApproverKind.ROLE,
                    approver_id=str(ROLE),
                    sod_rule=SoDRule.CANNOT_BE_PREVIOUS_APPROVER,
                ),
            ),
        ),
    )
    request_id = _open_tenant(db)
    record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    with pytest.raises(Exception, match="earlier level"):
        record_tenant_decision(
            db,
            tenant_id=TENANT,
            request_id=request_id,
            actor=_actor(ALICE),
            action=DecisionAction.APPROVE,
            content_digest=DIGEST,
        )


# ── The tenant scope is part of every lookup ────────────────────────────────


def test_another_tenants_request_is_not_addressable(db: Session) -> None:
    """SQLite has no RLS, so this proves only that the SERVICE scopes its
    lookups. The database-enforced half is `tests/test_approvals_isolation.py`."""
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    with pytest.raises(PolicyNotFound):
        evaluate_tenant_approval(db, tenant_id=OTHER_TENANT, request_id=request_id)


# ── The platform plane runs the same rules ──────────────────────────────────


def test_the_platform_plane_completes_the_same_lifecycle(db: Session) -> None:
    publish_platform_policy_version(db, revision=_revision(quorum=2))
    opened = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-77",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-77",
    )
    assert [event.event_type for event in opened.events] == ["approval.requested"]

    record_platform_decision(
        db,
        request_id=opened.request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    final = record_platform_decision(
        db,
        request_id=opened.request_id,
        actor=_actor(BOB),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    assert final.state is ApprovalState.APPROVED
    assert (
        evaluate_platform_approval(db, request_id=opened.request_id).state
        is ApprovalState.APPROVED
    )


def test_the_platform_plane_refuses_self_approval_and_cancels_by_requester(
    db: Session,
) -> None:
    publish_platform_policy_version(db, revision=_revision())
    opened = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-78",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-78",
    )
    with pytest.raises(Exception, match="does not permit the requester"):
        record_platform_decision(
            db,
            request_id=opened.request_id,
            actor=_actor(REQUESTER),
            action=DecisionAction.APPROVE,
            content_digest=DIGEST,
        )
    assert (
        cancel_platform_request(
            db,
            request_id=opened.request_id,
            cancelled_by=REQUESTER,
            reason="withdrawn",
        ).state
        is ApprovalState.CANCELLED
    )
