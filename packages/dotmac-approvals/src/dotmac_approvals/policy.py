"""The approval decision itself — pure, and shared by both planes.

Nothing here imports a session, a model or a plane. That is the point: ADR-0026
§ 5 requires the tenant and platform surfaces to be named separately, and the
only honest way to do that without maintaining two rule engines is to make the
rules a function of values. Every refusal is a typed error from `contracts`, so
a caller gets the same answer whichever plane it is on.

The ordering of checks is deliberate and is itself a security property:

1. **Is the request still open?** A terminal request accepts nothing.
2. **Is the content still the approved content?** Checked before eligibility, so
   a stale digest is refused even for an actor who would otherwise be allowed.
3. **Has this actor already decided this level?** Before quorum, so a duplicate
   vote can never be counted toward it.
4. **Is the level already satisfied?** Before eligibility, so a late-but-valid
   approver gets "already satisfied" rather than a misleading "not eligible".
5. **Is the actor eligible, and does SoD/self-approval/MFA permit it?**

Getting 3 before 4 is what makes two concurrent approvals by ONE actor count
once even if both pass the quorum check in memory — the database unique
constraint is the durable half of the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from dotmac_approvals.contracts import (
    Actor,
    ApprovalLevel,
    ApprovalState,
    ApproverKind,
    DecisionAction,
    DuplicateDecision,
    Evaluation,
    MFARequired,
    NotEligible,
    PolicyRevision,
    RecordedDecision,
    SelfApprovalRefused,
    SoDRule,
    SoDViolation,
)


def approvals_at(
    decisions: Iterable[RecordedDecision], level: int
) -> tuple[RecordedDecision, ...]:
    """Every APPROVE recorded at one level, in order."""
    return tuple(
        decision
        for decision in decisions
        if decision.level == level and decision.action is DecisionAction.APPROVE
    )


def distinct_approvers(decisions: Iterable[RecordedDecision]) -> frozenset[UUID]:
    """The set of actors who approved — a set, because quorum counts PEOPLE.

    Vendor CP made distinctness a unique constraint rather than a count, and it
    is the delta that matters most here: counting rows would let one determined
    actor satisfy a two-person quorum alone.
    """
    return frozenset(decision.actor_id for decision in decisions)


def level_satisfied(
    level: ApprovalLevel, decisions: Sequence[RecordedDecision]
) -> bool:
    """Has this level's quorum been met by DISTINCT actors?"""
    approved = distinct_approvers(approvals_at(decisions, level.sequence))
    return len(approved) >= level.quorum


def check_eligibility(level: ApprovalLevel, actor: Actor) -> None:
    """Does this level name this actor? Raises `NotEligible` if not.

    Role membership arrives on the `Actor`; the module never queries an identity
    estate to find out (see `contracts.Actor`).
    """
    if level.approver_kind is ApproverKind.USER:
        if str(actor.actor_id) != level.approver_id:
            raise NotEligible(
                f"level {level.sequence} names a specific user; "
                f"{actor.actor_id} is not it"
            )
        return

    # ApproverKind.ROLE
    if not any(str(role_id) == level.approver_id for role_id in actor.role_ids):
        raise NotEligible(
            f"level {level.sequence} requires role {level.approver_id}; the actor "
            "holds none of it"
        )


def check_sod(
    level: ApprovalLevel,
    *,
    actor: Actor,
    requested_by: UUID,
    decisions: Sequence[RecordedDecision],
) -> None:
    """Separation of duties, ported from ERP's `sod_rule` handling."""
    if level.sod_rule is None:
        return
    if level.sod_rule is SoDRule.CANNOT_BE_REQUESTER:
        if actor.actor_id == requested_by:
            raise SoDViolation(
                f"level {level.sequence} forbids the requester from approving"
            )
        return
    if level.sod_rule is SoDRule.CANNOT_BE_PREVIOUS_APPROVER:
        earlier = {
            decision.actor_id
            for decision in decisions
            if decision.level != level.sequence
        }
        if actor.actor_id in earlier:
            raise SoDViolation(
                f"level {level.sequence} forbids an actor who decided an earlier "
                "level from deciding this one"
            )


def check_self_approval(
    policy: PolicyRevision, *, actor: Actor, requested_by: UUID
) -> None:
    """The policy-wide self-approval switch (Vendor CP delta).

    Distinct from `CANNOT_BE_REQUESTER`, which is per level. This one is a
    property of the whole revision, and it defaults to refusing — a policy that
    silently allowed self-approval unless someone remembered to forbid it would
    make the unsafe case the default.
    """
    if not policy.allow_self_approval and actor.actor_id == requested_by:
        raise SelfApprovalRefused(
            f"policy {policy.policy_code} v{policy.version} does not permit the "
            "requester to approve their own request"
        )


def check_mfa(level: ApprovalLevel, actor: Actor) -> None:
    if level.requires_mfa and not actor.mfa_verified:
        raise MFARequired(
            f"level {level.sequence} requires verified MFA for this decision"
        )


def check_not_duplicate(
    level: ApprovalLevel, *, actor: Actor, decisions: Sequence[RecordedDecision]
) -> None:
    """One actor, one vote per level.

    In memory this is a refusal; durably it is a unique constraint. Both exist
    on purpose: the constraint is what holds under concurrency, and this is what
    gives a caller a typed error instead of an integrity error.
    """
    if any(
        decision.actor_id == actor.actor_id and decision.level == level.sequence
        for decision in decisions
    ):
        raise DuplicateDecision(
            f"{actor.actor_id} already decided level {level.sequence}"
        )


def authorise_approval(
    policy: PolicyRevision,
    *,
    current_level: int,
    actor: Actor,
    requested_by: UUID,
    decisions: Sequence[RecordedDecision],
) -> ApprovalLevel:
    """Every check an APPROVE must pass, in the order the module docstring sets
    out. Returns the level the decision lands on."""
    level = policy.level(current_level)
    check_not_duplicate(level, actor=actor, decisions=decisions)
    if level_satisfied(level, decisions):
        raise DuplicateDecision(
            f"level {level.sequence} is already satisfied; a further approval "
            "cannot change its outcome"
        )
    check_eligibility(level, actor)
    check_self_approval(policy, actor=actor, requested_by=requested_by)
    check_sod(level, actor=actor, requested_by=requested_by, decisions=decisions)
    check_mfa(level, actor)
    return level


def evaluate(
    policy: PolicyRevision,
    *,
    state: ApprovalState,
    current_level: int,
    decisions: Sequence[RecordedDecision],
) -> Evaluation:
    """The whole answer for a request in a given state.

    A terminal state is reported as recorded rather than recomputed: what was
    decided under a policy revision stays decided, even if the levels are read
    again later.
    """
    if state is ApprovalState.REJECTED:
        return Evaluation(
            state=state,
            current_level=current_level,
            total_levels=policy.total_levels,
            satisfied_levels=_satisfied_count(policy, decisions),
            reason="rejected",
        )
    if state is ApprovalState.CANCELLED:
        return Evaluation(
            state=state,
            current_level=current_level,
            total_levels=policy.total_levels,
            satisfied_levels=_satisfied_count(policy, decisions),
            reason="cancelled",
        )

    satisfied = _satisfied_count(policy, decisions)
    if satisfied >= policy.total_levels:
        return Evaluation(
            state=ApprovalState.APPROVED,
            current_level=policy.total_levels,
            total_levels=policy.total_levels,
            satisfied_levels=satisfied,
            reason="satisfied",
        )
    return Evaluation(
        state=ApprovalState.PENDING,
        current_level=min(satisfied + 1, policy.total_levels),
        total_levels=policy.total_levels,
        satisfied_levels=satisfied,
        reason="pending",
    )


def _satisfied_count(
    policy: PolicyRevision, decisions: Sequence[RecordedDecision]
) -> int:
    """How many ORDERED levels are satisfied, counting from the first.

    Ordered, not total: satisfying level 2 while level 1 is outstanding advances
    nothing, because the levels are a sequence rather than a checklist.
    """
    satisfied = 0
    for level in policy.levels:
        if not level_satisfied(level, decisions):
            break
        satisfied += 1
    return satisfied


__all__ = [
    "approvals_at",
    "authorise_approval",
    "check_eligibility",
    "check_mfa",
    "check_not_duplicate",
    "check_self_approval",
    "check_sod",
    "distinct_approvers",
    "evaluate",
    "level_satisfied",
]
