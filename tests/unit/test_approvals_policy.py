"""The approval rules, exercised as values (ADR-0026).

This is the parity suite for the half that ERP and the vendor control plane each
implemented differently. Every test here names which source it preserves, so a
later change that "simplifies" one of them has to argue with the product it came
from rather than with a preference.

Pure by construction: no session, no models, no plane. If any assertion here
needs a database, the boundary has moved and that is the finding.
"""

from __future__ import annotations

import uuid

import pytest
from dotmac_approvals.contracts import (
    Actor,
    ApprovalLevel,
    ApprovalState,
    ApproverKind,
    ContentChanged,
    DecisionAction,
    DuplicateDecision,
    InvalidPolicy,
    MFARequired,
    NotEligible,
    PolicyRevision,
    RecordedDecision,
    SelfApprovalRefused,
    SoDRule,
    SoDViolation,
    validate_digest,
)
from dotmac_approvals.policy import (
    authorise_approval,
    distinct_approvers,
    evaluate,
    level_satisfied,
)

REQUESTER = uuid.uuid4()
ALICE = uuid.uuid4()
BOB = uuid.uuid4()
ROLE = uuid.uuid4()


def _level(sequence: int = 1, **overrides: object) -> ApprovalLevel:
    return ApprovalLevel(
        sequence=sequence,
        approver_kind=ApproverKind.ROLE,
        approver_id=str(ROLE),
        **overrides,  # type: ignore[arg-type]
    )


def _policy(*levels: ApprovalLevel, self_approval: bool = False) -> PolicyRevision:
    return PolicyRevision(
        policy_code="payment.release",
        version=1,
        levels=levels or (_level(),),
        allow_self_approval=self_approval,
    )


def _actor(actor_id: uuid.UUID, *, mfa: bool = False) -> Actor:
    return Actor(actor_id=actor_id, role_ids=frozenset({ROLE}), mfa_verified=mfa)


def _approved(actor_id: uuid.UUID, level: int = 1) -> RecordedDecision:
    return RecordedDecision(
        level=level, actor_id=actor_id, action=DecisionAction.APPROVE
    )


# ── Policy shape (Vendor CP: immutability; ERP: ordered levels) ──────────────


def test_a_policy_with_no_levels_is_refused() -> None:
    """A policy nobody has to satisfy would approve by default — the fail-open
    shape this module exists to avoid."""
    with pytest.raises(InvalidPolicy, match="declares no levels"):
        PolicyRevision(policy_code="x", version=1, levels=())


def test_levels_must_be_ordered_and_dense() -> None:
    """ERP stored levels as a JSONB array and indexed it positionally, so a gap
    or a repeat silently mis-selected the level a decision landed on."""
    with pytest.raises(InvalidPolicy, match="ORDERED and dense"):
        PolicyRevision(
            policy_code="x",
            version=1,
            levels=(_level(1), _level(3)),
        )


def test_a_level_needing_nobody_is_refused() -> None:
    with pytest.raises(InvalidPolicy, match="not a level"):
        _level(quorum=0)


def test_a_level_naming_no_approver_is_refused_at_construction() -> None:
    """ERP's own service refused this configuration — but only when someone
    tried to approve, which is far too late to learn the policy is unusable."""
    with pytest.raises(InvalidPolicy, match="names no approver"):
        ApprovalLevel(sequence=1, approver_kind=ApproverKind.USER, approver_id="  ")


def test_a_policy_revision_round_trips_through_its_document() -> None:
    """The persisted form must reconstruct the same revision, or an immutable
    version would not actually be immutable."""
    original = _policy(_level(1, quorum=2, sod_rule=SoDRule.CANNOT_BE_REQUESTER))
    assert PolicyRevision.from_document(original.as_document()) == original


# ── Quorum counts distinct PEOPLE (Vendor CP delta) ─────────────────────────


def test_one_actor_cannot_satisfy_a_two_person_quorum_alone() -> None:
    """The delta that matters most. Counting rows would let a determined actor
    approve twice; counting distinct actors is what a dual-control rule means."""
    level = _level(quorum=2)
    decisions = [_approved(ALICE), _approved(ALICE)]
    assert distinct_approvers(decisions) == {ALICE}
    assert not level_satisfied(level, decisions)


def test_two_distinct_actors_satisfy_a_two_person_quorum() -> None:
    level = _level(quorum=2)
    assert level_satisfied(level, [_approved(ALICE), _approved(BOB)])


def test_a_second_vote_from_the_same_actor_is_refused() -> None:
    policy = _policy()
    with pytest.raises(DuplicateDecision, match="already decided level 1"):
        authorise_approval(
            policy,
            current_level=1,
            actor=_actor(ALICE),
            requested_by=REQUESTER,
            decisions=[_approved(ALICE)],
        )


def test_approving_a_satisfied_level_is_refused() -> None:
    """A late-but-eligible approver gets a truthful reason rather than a
    misleading "not eligible"."""
    policy = _policy()
    with pytest.raises(DuplicateDecision, match="already satisfied"):
        authorise_approval(
            policy,
            current_level=1,
            actor=_actor(BOB),
            requested_by=REQUESTER,
            decisions=[_approved(ALICE)],
        )


# ── Eligibility (ERP: user and role) ────────────────────────────────────────


def test_a_user_level_names_exactly_one_actor() -> None:
    policy = _policy(
        ApprovalLevel(
            sequence=1, approver_kind=ApproverKind.USER, approver_id=str(ALICE)
        )
    )
    authorise_approval(
        policy,
        current_level=1,
        actor=Actor(actor_id=ALICE),
        requested_by=REQUESTER,
        decisions=[],
    )
    with pytest.raises(NotEligible, match="names a specific user"):
        authorise_approval(
            policy,
            current_level=1,
            actor=Actor(actor_id=BOB),
            requested_by=REQUESTER,
            decisions=[],
        )


def test_a_role_level_reads_membership_from_the_actor_not_a_database() -> None:
    """ERP joined PersonRole/Role inside the service. Taking membership as a
    value is what lets this module install beside a product whose RBAC the
    kernel has never seen."""
    policy = _policy()
    with pytest.raises(NotEligible, match="requires role"):
        authorise_approval(
            policy,
            current_level=1,
            actor=Actor(actor_id=ALICE, role_ids=frozenset()),
            requested_by=REQUESTER,
            decisions=[],
        )


# ── Separation of duties and self-approval ──────────────────────────────────


def test_self_approval_is_refused_by_default() -> None:
    """Vendor CP's `allow_self_approval` defaults to False, and so does this:
    the unsafe case must not be the one a policy author has to remember."""
    policy = _policy()
    with pytest.raises(SelfApprovalRefused):
        authorise_approval(
            policy,
            current_level=1,
            actor=_actor(REQUESTER),
            requested_by=REQUESTER,
            decisions=[],
        )


def test_self_approval_is_permitted_when_the_policy_says_so() -> None:
    policy = _policy(self_approval=True)
    authorise_approval(
        policy,
        current_level=1,
        actor=_actor(REQUESTER),
        requested_by=REQUESTER,
        decisions=[],
    )


def test_sod_can_forbid_the_requester_per_level() -> None:
    policy = _policy(_level(sod_rule=SoDRule.CANNOT_BE_REQUESTER), self_approval=True)
    with pytest.raises(SoDViolation, match="forbids the requester"):
        authorise_approval(
            policy,
            current_level=1,
            actor=_actor(REQUESTER),
            requested_by=REQUESTER,
            decisions=[],
        )


def test_sod_can_forbid_an_earlier_level_approver() -> None:
    """Dual control across levels: approving level 1 disqualifies you at 2."""
    policy = _policy(
        _level(1),
        _level(2, sod_rule=SoDRule.CANNOT_BE_PREVIOUS_APPROVER),
    )
    with pytest.raises(SoDViolation, match="earlier level"):
        authorise_approval(
            policy,
            current_level=2,
            actor=_actor(ALICE),
            requested_by=REQUESTER,
            decisions=[_approved(ALICE, level=1)],
        )


def test_mfa_is_required_when_the_level_declares_it() -> None:
    policy = _policy(_level(requires_mfa=True))
    with pytest.raises(MFARequired):
        authorise_approval(
            policy,
            current_level=1,
            actor=_actor(ALICE, mfa=False),
            requested_by=REQUESTER,
            decisions=[],
        )
    authorise_approval(
        policy,
        current_level=1,
        actor=_actor(ALICE, mfa=True),
        requested_by=REQUESTER,
        decisions=[],
    )


# ── Ordered evaluation (ERP: level advancement) ─────────────────────────────


def test_levels_advance_in_order_and_out_of_order_approval_advances_nothing() -> None:
    """Levels are a sequence, not a checklist: satisfying level 2 while level 1
    is outstanding must not approve the request."""
    policy = _policy(_level(1), _level(2))
    outcome = evaluate(
        policy,
        state=ApprovalState.PENDING,
        current_level=1,
        decisions=[_approved(ALICE, level=2)],
    )
    assert outcome.state is ApprovalState.PENDING
    assert outcome.satisfied_levels == 0


def test_the_final_level_completing_approves_the_request() -> None:
    policy = _policy(_level(1), _level(2))
    outcome = evaluate(
        policy,
        state=ApprovalState.PENDING,
        current_level=2,
        decisions=[_approved(ALICE, level=1), _approved(BOB, level=2)],
    )
    assert outcome.state is ApprovalState.APPROVED
    assert outcome.reason == "satisfied"
    assert outcome.satisfied_levels == outcome.total_levels


def test_a_terminal_state_is_reported_as_recorded_not_recomputed() -> None:
    """What was decided under a revision stays decided, even when the levels are
    read again later."""
    policy = _policy()
    outcome = evaluate(
        policy,
        state=ApprovalState.REJECTED,
        current_level=1,
        decisions=[_approved(ALICE)],
    )
    assert outcome.state is ApprovalState.REJECTED
    assert outcome.reason == "rejected"


# ── Content binding ─────────────────────────────────────────────────────────


def test_a_digest_must_look_like_a_digest() -> None:
    """The digest is the thing an approval is FOR. A caller that passed a
    subject id here would otherwise bind the approval to a value that never
    changes when the content does."""
    validate_digest("sha256:" + "a" * 64)
    for bad in ("sha256:short", "md5:" + "a" * 64, "sha256:" + "A" * 64):
        with pytest.raises(ContentChanged):
            validate_digest(bad)
