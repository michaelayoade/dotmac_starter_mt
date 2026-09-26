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
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_approvals.contracts import (
    Actor,
    ApprovalHoldRefusal,
    ApprovalLevel,
    ApprovalNotHeld,
    ApprovalState,
    ApproverKind,
    ContentChanged,
    DecisionAction,
    HeldPlatformApproval,
    NotRequester,
    PolicyNotFound,
    PolicyRevision,
    PolicyVersionExists,
    RequestNotPending,
    SoDRule,
    WithdrawalReferenceConflict,
    WithdrawalRefused,
)
from dotmac_approvals.models import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalWithdrawal,
    PlatformApprovalDecision,
    PlatformApprovalPolicy,
    PlatformApprovalRequest,
    PlatformApprovalWithdrawal,
)
from dotmac_approvals.service import (
    _withdraw_platform_approval as withdraw_platform_approval,
)
from dotmac_approvals.service import (
    _withdraw_tenant_approval as withdraw_tenant_approval,
)
from dotmac_approvals.service import (
    cancel_platform_request,
    cancel_tenant_request,
    evaluate_platform_approval,
    evaluate_tenant_approval,
    get_platform_request,
    get_tenant_request,
    hold_platform_approval,
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
        ApprovalWithdrawal,
        PlatformApprovalPolicy,
        PlatformApprovalRequest,
        PlatformApprovalDecision,
        PlatformApprovalWithdrawal,
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


def test_tenant_withdrawal_preserves_approval_and_replays_exact_evidence(
    db: Session,
) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    approved_row = db.get(ApprovalRequest, request_id)
    assert approved_row is not None and approved_row.completed_at is not None
    approved_at = approved_row.completed_at
    command = {
        "tenant_id": TENANT,
        "request_id": request_id,
        "actor": _actor(BOB),
        "authority_ref": "security-review-7",
        "reason": "superseded authority",
        "external_ref": "control-revoke-7",
    }
    first = withdraw_tenant_approval(db, **command)
    assert first.state is ApprovalState.WITHDRAWN
    assert first.evaluation.reason == "withdrawn"
    event = first.events[0]
    assert event.event_type == "approval.withdrawn"
    assert event.withdrawal is not None
    assert event.withdrawal.approved_at.replace(tzinfo=None) == approved_at.replace(
        tzinfo=None
    )
    assert event.payload()["external_ref"] == "control-revoke-7"
    assert event.payload()["content_digest"] == DIGEST
    assert withdraw_tenant_approval(db, **command).events == ()
    assert len(db.execute(select(ApprovalWithdrawal)).scalars().all()) == 1
    current_row = db.get(ApprovalRequest, request_id)
    assert current_row is not None and current_row.completed_at == approved_at
    detail = get_tenant_request(db, tenant_id=TENANT, request_id=request_id)
    assert detail is not None
    assert detail.evaluation.is_approved is False
    assert detail.withdrawal is not None
    assert detail.withdrawal.withdrawal_id == event.withdrawal.withdrawal_id
    assert len(detail.decisions) == 1
    assert detail.decisions[0].action is DecisionAction.APPROVE
    with pytest.raises(WithdrawalReferenceConflict):
        withdraw_tenant_approval(db, **{**command, "reason": "different"})
    with pytest.raises(WithdrawalReferenceConflict):
        withdraw_tenant_approval(db, **{**command, "external_ref": "another"})


def test_withdrawal_refuses_unapproved_requests_and_empty_authority(
    db: Session,
) -> None:
    publish_tenant_policy_version(db, tenant_id=TENANT, revision=_revision())
    request_id = _open_tenant(db)
    command = {
        "tenant_id": TENANT,
        "request_id": request_id,
        "actor": _actor(BOB),
        "authority_ref": "review-1",
        "reason": "invalid approval",
        "external_ref": "revoke-1",
    }
    with pytest.raises(WithdrawalRefused, match="completed approval"):
        withdraw_tenant_approval(db, **command)
    record_tenant_decision(
        db,
        tenant_id=TENANT,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.REJECT,
        content_digest=DIGEST,
    )
    with pytest.raises(WithdrawalRefused, match="completed approval"):
        withdraw_tenant_approval(db, **command)
    with pytest.raises(WithdrawalRefused, match="authority_ref"):
        withdraw_tenant_approval(db, **{**command, "authority_ref": " "})


def test_platform_withdrawal_is_separate_and_one_per_request(db: Session) -> None:
    publish_platform_policy_version(db, revision=_revision())
    first_request = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-1",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-1",
    ).request_id
    record_platform_decision(
        db,
        request_id=first_request,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    command = {
        "request_id": first_request,
        "actor": _actor(BOB),
        "authority_ref": "cp-review-1",
        "reason": "plan invalidated",
        "external_ref": "control-revoke-1",
    }
    result = withdraw_platform_approval(db, **command)
    assert result.state is ApprovalState.WITHDRAWN
    assert result.events[0].payload()["subject_id"] == "plan-1"
    assert withdraw_platform_approval(db, **command).events == ()
    assert len(db.execute(select(PlatformApprovalWithdrawal)).scalars().all()) == 1
    detail = get_platform_request(db, request_id=first_request)
    assert detail is not None and detail.withdrawal is not None
    assert len(detail.decisions) == 1
    second_request = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-2",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-2",
    ).request_id
    record_platform_decision(
        db,
        request_id=second_request,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    with pytest.raises(WithdrawalReferenceConflict, match="another request"):
        withdraw_platform_approval(db, **{**command, "request_id": second_request})


def test_withdrawal_contract_refuses_backdating_and_inconsistent_detail(
    db: Session,
) -> None:
    publish_platform_policy_version(db, revision=_revision())
    request_id = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-contract",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-contract",
    ).request_id
    record_platform_decision(
        db,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    outcome = withdraw_platform_approval(
        db,
        request_id=request_id,
        actor=_actor(BOB),
        authority_ref="cp-review-contract",
        reason="plan invalidated",
        external_ref="control-revoke-contract",
    )
    evidence = outcome.events[0].withdrawal
    assert evidence is not None
    with pytest.raises(ValueError, match="before its approval"):
        replace(
            evidence,
            effective_at=evidence.approved_at - timedelta(microseconds=1),
        )

    detail = get_platform_request(db, request_id=request_id)
    assert detail is not None and detail.withdrawal is not None
    with pytest.raises(ValueError, match="required exactly"):
        replace(detail, withdrawal=None)


# ── hold_platform_approval: the synchronous barrier a dependent transition holds ─


def _approved_platform(db: Session, *, subject_id: str = "plan-hold") -> uuid.UUID:
    publish_platform_policy_version(db, revision=_revision())
    request_id = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id=subject_id,
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key=subject_id,
    ).request_id
    record_platform_decision(
        db,
        request_id=request_id,
        actor=_actor(ALICE),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    return request_id


def test_a_standing_platform_approval_is_held_with_its_evidence(db: Session) -> None:
    """POSITIVE CONTROL for every refusal below."""
    request_id = _approved_platform(db)
    held = hold_platform_approval(
        db,
        request_id=request_id,
        subject_type="fleet.plan",
        subject_id="plan-hold",
        content_digest=DIGEST,
    )
    assert isinstance(held, HeldPlatformApproval)
    assert held.request_id == request_id
    assert held.content_digest == DIGEST
    assert held.policy_code == "payment.release"
    assert held.policy_version == 1
    assert held.approver_ids == (ALICE,)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"request_id": uuid.UUID(int=7)}, ApprovalHoldRefusal.REQUEST_NOT_FOUND),
        ({"subject_type": "other.plan"}, ApprovalHoldRefusal.SUBJECT_MISMATCH),
        ({"subject_id": "plan-other"}, ApprovalHoldRefusal.SUBJECT_MISMATCH),
        ({"content_digest": OTHER_DIGEST}, ApprovalHoldRefusal.DIGEST_MISMATCH),
    ],
)
def test_a_hold_refuses_a_request_that_does_not_bind_this_subject(
    db: Session, overrides: dict[str, object], code: ApprovalHoldRefusal
) -> None:
    request_id = _approved_platform(db)
    arguments: dict[str, object] = {
        "request_id": request_id,
        "subject_type": "fleet.plan",
        "subject_id": "plan-hold",
        "content_digest": DIGEST,
        **overrides,
    }
    with pytest.raises(ApprovalNotHeld) as refused:
        hold_platform_approval(db, **arguments)  # type: ignore[arg-type]
    assert refused.value.code is code


def test_a_pending_request_is_not_held(db: Session) -> None:
    publish_platform_policy_version(db, revision=_revision())
    request_id = request_platform_approval(
        db,
        policy_code="payment.release",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id="plan-pending",
        content_digest=DIGEST,
        requested_by=REQUESTER,
        idempotency_key="plan-pending",
    ).request_id
    with pytest.raises(ApprovalNotHeld) as refused:
        hold_platform_approval(
            db,
            request_id=request_id,
            subject_type="fleet.plan",
            subject_id="plan-pending",
            content_digest=DIGEST,
        )
    assert refused.value.code is ApprovalHoldRefusal.NOT_APPROVED


def test_a_withdrawn_approval_is_not_held(db: Session) -> None:
    """Withdrawn is its own code, distinct from never-approved."""
    request_id = _approved_platform(db)
    withdraw_platform_approval(
        db,
        request_id=request_id,
        actor=_actor(BOB),
        authority_ref="cp-review-hold",
        reason="plan invalidated",
        external_ref="control-revoke-hold",
    )
    with pytest.raises(ApprovalNotHeld) as refused:
        hold_platform_approval(
            db,
            request_id=request_id,
            subject_type="fleet.plan",
            subject_id="plan-hold",
            content_digest=DIGEST,
        )
    assert refused.value.code is ApprovalHoldRefusal.WITHDRAWN


def test_an_approved_row_without_an_approve_decision_is_not_held(db: Session) -> None:
    """A standing claim needs a vote behind it; the row alone is not evidence."""
    request_id = _approved_platform(db)
    for decision in db.execute(select(PlatformApprovalDecision)).scalars():
        db.delete(decision)
    db.flush()
    with pytest.raises(ApprovalNotHeld) as refused:
        hold_platform_approval(
            db,
            request_id=request_id,
            subject_type="fleet.plan",
            subject_id="plan-hold",
            content_digest=DIGEST,
        )
    assert refused.value.code is ApprovalHoldRefusal.NO_APPROVE_DECISION
