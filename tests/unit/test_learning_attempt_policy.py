"""The attempt entitlement, and the grant that changes it.

The arithmetic is tested without a database wherever it can be, because the
whole point of the module is that five callers share one expression — a pure
function they can all reach is what makes that true.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_learning import attempt_policy
from dotmac_learning.assessment_reads import FeedbackRevealTerms

FEEDBACK = FeedbackRevealTerms(
    {"practice": "immediate", "graded": "on_pass_or_exhausted", "exam": "never"}
)


class _Activity:
    """Only what the entitlement reads."""

    def __init__(self, max_attempts, min_attempt_interval_seconds=0):
        self.max_attempts = max_attempts
        self.min_attempt_interval_seconds = min_attempt_interval_seconds


def test_uncapped_activity_is_never_exhausted():
    ent = attempt_policy.entitlement(_Activity(None), used=99)
    assert ent.exhausted is False
    # None, not a large number: callers render "unlimited", and a number would
    # invite them to count down from it.
    assert ent.remaining is None


def test_capped_activity_counts_down_and_then_exhausts():
    act = _Activity(3)
    assert attempt_policy.entitlement(act, used=0).remaining == 3
    assert attempt_policy.entitlement(act, used=2).remaining == 1
    assert attempt_policy.entitlement(act, used=2).exhausted is False
    assert attempt_policy.entitlement(act, used=3).exhausted is True


def test_remaining_never_goes_negative():
    """A grant withdrawn by lowering max_attempts must not render as -1 left."""
    assert attempt_policy.entitlement(_Activity(2), used=5).remaining == 0


def test_a_grant_raises_the_limit_for_that_learner_only():
    """The whole reason grants exist: max_attempts alone reopens it for everyone."""
    act = _Activity(2)
    assert attempt_policy.entitlement(act, used=2).exhausted is True
    assert attempt_policy.entitlement(act, used=2, granted=1).exhausted is False
    assert attempt_policy.entitlement(act, used=2, granted=1).remaining == 1


def test_grants_accumulate():
    """Two separate grants of one are three attempts, not two."""
    assert attempt_policy.entitlement(_Activity(1), used=1, granted=2).remaining == 2


def test_a_grant_on_an_uncapped_activity_changes_nothing():
    ent = attempt_policy.entitlement(_Activity(None), used=4, granted=3)
    assert ent.limit is None and ent.exhausted is False


@pytest.mark.parametrize(
    "max_attempts, used, granted, limit, remaining, exhausted",
    [
        (3, 2, 0, 3, 1, False),
        (3, 3, 0, 3, 0, True),
        (2, 2, 1, 3, 1, False),
        (1, 1, 2, 3, 2, False),
        (2, 5, 0, 2, 0, True),
        (None, 99, 3, None, None, False),
    ],
)
def test_entitlement_with_grants_reads_bound_total_once(
    max_attempts, used, granted, limit, remaining, exhausted
):
    class Reader:
        calls = 0

        def total(self):
            self.calls += 1
            return granted

    reader = Reader()
    ent = attempt_policy.entitlement_with_grants(
        _Activity(max_attempts), used=used, reader=reader
    )

    assert ent == attempt_policy.Entitlement(used=used, granted=granted, limit=limit)
    assert ent.remaining == remaining
    assert ent.exhausted is exhausted
    assert reader.calls == 1


def test_entitlement_with_grants_propagates_reader_failure():
    failure = RuntimeError("grant read failed")

    class Reader:
        def total(self):
            raise failure

    with pytest.raises(RuntimeError) as raised:
        attempt_policy.entitlement_with_grants(_Activity(2), used=1, reader=Reader())

    assert raised.value is failure


def test_for_learner_uses_supplied_session_and_exact_grant_scope(monkeypatch):
    class _Db:
        def __init__(self):
            self.scalar_calls = 0

        def scalar(self, statement):
            self.scalar_calls += 1
            return None

    db = _Db()
    tenant_id, person_id, activity_id = uuid4(), uuid4(), uuid4()
    activity = _Activity(2)
    activity.id = activity_id
    calls = []
    narrow_calls = []
    narrow_entry = attempt_policy.entitlement_with_grants

    def granted_for(supplied_db, *, tenant_id, person_id, activity_id):
        calls.append((supplied_db, tenant_id, person_id, activity_id))
        return 3

    def entitlement_with_grants(
        supplied_activity, *, used, reader, last_attempt_at=None
    ):
        narrow_calls.append((supplied_activity, used))
        return narrow_entry(
            supplied_activity, used=used, reader=reader, last_attempt_at=last_attempt_at
        )

    monkeypatch.setattr(attempt_policy, "granted_for", granted_for)
    monkeypatch.setattr(
        attempt_policy, "entitlement_with_grants", entitlement_with_grants
    )

    ent = attempt_policy.for_learner(
        db, tenant_id=tenant_id, person_id=person_id, activity=activity, used=4
    )

    assert len(calls) == 1
    supplied_db, supplied_tenant, supplied_person, supplied_activity = calls[0]
    assert supplied_db is db
    assert supplied_tenant is tenant_id
    assert supplied_person is person_id
    assert supplied_activity is activity_id
    assert narrow_calls == [(activity, 4)]
    assert ent == attempt_policy.Entitlement(used=4, granted=3, limit=5)
    assert ent.remaining == 1
    assert ent.exhausted is False
    # Interval 0 (the _Activity default) adds no last-attempt query (A-P3-5).
    assert db.scalar_calls == 0
    cooling = _Activity(2, min_attempt_interval_seconds=60)
    cooling.id = activity_id
    attempt_policy.for_learner(
        db, tenant_id=tenant_id, person_id=person_id, activity=cooling, used=1
    )
    assert db.scalar_calls == 1


def test_reveal_feedback_does_not_leak_the_key_before_a_granted_retake():
    """The failure this prevents.

    On a graded activity the answer key is revealed once attempts are spent.
    Granting a retake after that would hand the learner the answers to the
    attempt they are about to sit — so the reveal has to consult the same
    entitlement the submit path does, grants included.
    """
    from dotmac_learning.assessment_reads import reveal_feedback

    act = _Activity(1)
    act.assessment_mode = "graded"

    # Spent, not passed: the key is shown, which is the point of the mode.
    assert reveal_feedback(act, terms=FEEDBACK, passed=False, attempts_used=1) is True
    # ...but once a retake is granted, it is no longer spent, and the key closes.
    assert (
        reveal_feedback(
            act, terms=FEEDBACK, passed=False, attempts_used=1, attempts_granted=1
        )
        is False
    )


@pytest.mark.parametrize("mode", ["practice", "exam", "graded", "other"])
@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize("maximum,used,granted", [(None, 99, 0), (1, 1, 0), (1, 1, 1)])
def test_feedback_modes_use_original_entitlement(mode, passed, maximum, used, granted):
    from dotmac_learning.assessment_reads import reveal_feedback

    act = _Activity(maximum)
    act.assessment_mode = mode
    expected = (
        True
        if mode == "practice"
        else False
        if mode == "exam"
        else passed
        or attempt_policy.entitlement(act, used=used, granted=granted).exhausted
    )
    assert (
        reveal_feedback(
            act,
            terms=FEEDBACK,
            passed=passed,
            attempts_used=used,
            attempts_granted=granted,
        )
        == expected
    )


def test_commands_are_frozen_extra_forbid_and_audit_context_is_separate():
    from dataclasses import FrozenInstanceError

    from dotmac_learning.attempt_contracts import (
        AttemptAuditContext,
        CloseAttemptCommand,
        GrantExtraAttemptCommand,
        OpenAttemptCommand,
    )
    from pydantic import ValidationError

    scope = {"tenant_id": uuid4(), "person_id": uuid4(), "activity_id": uuid4()}
    for model, fields in (
        (CloseAttemptCommand, {}),
        (GrantExtraAttemptCommand, {"reason": "power cut", "extra_attempts": 1}),
        (OpenAttemptCommand, {"all_ext_ids": ["q"], "count": 1}),
    ):
        payload = model(**scope, **fields)
        with pytest.raises(ValidationError):
            payload.tenant_id = uuid4()
        with pytest.raises(ValidationError):
            model(**scope, **fields, actor_type="system")
    authority = AttemptAuditContext(
        tenant_id=scope["tenant_id"], actor_type="system", source_application="academy"
    )
    with pytest.raises(FrozenInstanceError):
        authority.actor_type = "user"


@pytest.mark.parametrize("actor_type", ["system", "user", "api_key", "service"])
def test_audit_context_accepts_each_kernel_actor_type_and_preserves_attribution(
    actor_type,
):
    from dataclasses import FrozenInstanceError

    from dotmac_learning.attempt_contracts import AttemptAuditContext

    tenant_id = uuid4()
    context = AttemptAuditContext(
        tenant_id=tenant_id,
        actor_type=actor_type,
        source_application="academy",
        actor_id="principal-123",
        actor_label="Display snapshot",
    )
    assert context.tenant_id is tenant_id
    assert context.actor_type == actor_type
    assert context.actor_id == "principal-123"
    assert context.actor_label == "Display snapshot"
    assert context.source_application == "academy"
    with pytest.raises(FrozenInstanceError):
        context.actor_type = "system"
    with pytest.raises(FrozenInstanceError):
        context.actor_id = "replacement"


def test_audit_context_actor_annotation_matches_public_kernel_vocabulary():
    from typing import get_args, get_type_hints

    from dotmac_kernel.audit import ACTOR_TYPES
    from dotmac_learning.attempt_contracts import AttemptAuditContext

    declared_types = frozenset(
        get_args(get_type_hints(AttemptAuditContext)["actor_type"])
    )
    assert declared_types == ACTOR_TYPES == {"system", "user", "api_key", "service"}


@pytest.mark.parametrize(
    "actor_type", ["", "USER", " user", "user ", "anonymous", "tenant_admin", None, 7]
)
def test_audit_context_constructor_refuses_unknown_actor_types(actor_type):
    from dotmac_learning.attempt_contracts import AttemptAuditContext

    with pytest.raises(ValueError, match="attempt audit actor_type must be"):
        AttemptAuditContext(
            tenant_id=uuid4(), actor_type=actor_type, source_application="academy"
        )


@pytest.mark.parametrize("actor_type", ["user", "api_key", "service"])
@pytest.mark.parametrize("actor_id", [None, "", " "])
def test_audit_context_keeps_non_system_id_validation_at_kernel_writer(
    actor_type, actor_id
):
    from dotmac_kernel.audit import MissingAuditActorError, resolve_audit_actor
    from dotmac_learning.attempt_contracts import AttemptAuditContext

    context = AttemptAuditContext(
        tenant_id=uuid4(),
        actor_type=actor_type,
        source_application="academy",
        actor_id=actor_id,
    )
    assert context.actor_id == actor_id
    with pytest.raises(MissingAuditActorError):
        resolve_audit_actor(
            actor_type=context.actor_type,
            actor_id=context.actor_id,
            actor_party_id=None,
        )


@pytest.mark.parametrize("actor_id", [None, "scheduled-job-123"])
def test_audit_context_preserves_both_system_identifier_forms(actor_id):
    from dotmac_kernel.audit import resolve_audit_actor
    from dotmac_learning.attempt_contracts import AttemptAuditContext

    context = AttemptAuditContext(
        tenant_id=uuid4(),
        actor_type="system",
        source_application="academy",
        actor_id=actor_id,
    )
    assert context.actor_id == actor_id
    assert resolve_audit_actor(
        actor_type=context.actor_type, actor_id=context.actor_id, actor_party_id=None
    ) == ("system", actor_id)


def test_cooldown_horizon_is_not_exhaustion():
    from datetime import UTC, datetime, timedelta

    last = datetime(2026, 1, 1, tzinfo=UTC)
    cooling = _Activity(3, min_attempt_interval_seconds=3600)
    ent = attempt_policy.entitlement(cooling, used=1, last_attempt_at=last)
    assert ent.next_allowed_at == last + timedelta(hours=1)
    assert ent.exhausted is False and ent.remaining == 2
    # Interval 0 (the default) never produces a horizon.
    plain = attempt_policy.entitlement(_Activity(3), used=1, last_attempt_at=last)
    assert plain.next_allowed_at is None
    # No previous attempt: nothing to cool down from.
    first = attempt_policy.entitlement(cooling, used=0)
    assert first.next_allowed_at is None
    # A naive last attempt (SQLite round-trip) is read as UTC, so the
    # horizon is aware whenever set (A-P3-4).
    naive = attempt_policy.entitlement(
        cooling, used=1, last_attempt_at=datetime(2026, 1, 1)
    )
    assert naive.next_allowed_at == last + timedelta(hours=1)
    assert naive.next_allowed_at.tzinfo is not None
