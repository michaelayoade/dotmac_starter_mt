"""Escalation policy and instance behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_operational_escalations import models
from dotmac_operational_escalations.contracts import (
    Conflict,
    DraftPolicyVersion,
    EscalationStatus,
    PolicyVersionState,
    RaiseEscalation,
    RegisterPolicy,
    SettleEscalation,
)
from dotmac_operational_escalations.models import (
    TENANT_TABLES,
    PolicyVersionImmutableError,
)
from dotmac_operational_escalations.service import (
    acknowledge_escalation,
    activate_policy_version,
    cancel_escalation,
    draft_policy_version,
    raise_escalation,
    register_policy,
    resolve_escalation,
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
        execution_options={"schema_translate_map": {"mod_escalations": None}},
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


def _policy(db: Session, scope: TenantScope, code: str = "outage-l1"):
    return register_policy(
        db,
        scope=scope,
        command=RegisterPolicy(
            code=code,
            name="Outage level 1",
            subject_type="outage",
            trigger="unresolved",
        ),
    )


def _active(db: Session, scope: TenantScope, policy, cooldown: int = 0):
    version = draft_policy_version(
        db,
        scope=scope,
        command=DraftPolicyVersion(
            policy_id=policy.id,
            level=1,
            channels=("email", "web"),
            cooldown_seconds=cooldown,
        ),
    )
    return activate_policy_version(db, scope=scope, version_id=version.id, at=NOW)


def test_activating_a_version_retires_the_one_it_replaces(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    first = _active(db, scope, policy)
    second_draft = draft_policy_version(
        db,
        scope=scope,
        command=DraftPolicyVersion(
            policy_id=policy.id, level=2, channels=("sms",), cooldown_seconds=0
        ),
    )
    second = activate_policy_version(
        db, scope=scope, version_id=second_draft.id, at=NOW + timedelta(days=1)
    )
    assert first.state is PolicyVersionState.RETIRED
    assert first.retired_at == NOW + timedelta(days=1)
    assert second.state is PolicyVersionState.ACTIVE
    assert (first.version, second.version) == (1, 2)


def test_an_instance_keeps_the_terms_it_was_raised_under(db: Session) -> None:
    """The defect this module exists not to repeat: editing Sub's mutable
    policy silently rewrote the terms every already-open escalation had been
    raised under, and nothing could read back what it said at raise time."""
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    first = _active(db, scope, policy)
    instance = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="outage:1:unresolved",
            raised_at=NOW,
        ),
    )
    assert instance.policy_version_id == first.id
    assert instance.level == 1

    later_draft = draft_policy_version(
        db,
        scope=scope,
        command=DraftPolicyVersion(
            policy_id=policy.id, level=3, channels=("sms",), cooldown_seconds=0
        ),
    )
    activate_policy_version(
        db, scope=scope, version_id=later_draft.id, at=NOW + timedelta(days=1)
    )
    assert instance.policy_version_id == first.id
    assert instance.level == 1


def test_published_version_terms_are_immutable(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    version = _active(db, scope, _policy(db, scope))
    version.level = 9
    with pytest.raises(PolicyVersionImmutableError):
        db.flush()
    db.expunge_all()


def test_raising_is_deduplicated_and_refused_inside_cooldown(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    _active(db, scope, policy, cooldown=3600)
    first = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="outage:1:unresolved",
            raised_at=NOW,
        ),
    )
    replay = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="outage:1:unresolved",
            raised_at=NOW,
        ),
    )
    assert replay.id == first.id

    with pytest.raises(Conflict):
        raise_escalation(
            db,
            scope=scope,
            command=RaiseEscalation(
                policy_id=policy.id,
                subject_reference="outage:1",
                dedup_key="outage:1:unresolved:second",
                raised_at=NOW + timedelta(minutes=5),
            ),
        )
    outside = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="outage:1:unresolved:later",
            raised_at=NOW + timedelta(hours=2),
        ),
    )
    assert outside.id != first.id


def test_a_policy_without_an_active_version_cannot_raise(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    draft_policy_version(
        db,
        scope=scope,
        command=DraftPolicyVersion(
            policy_id=policy.id, level=1, channels=("email",), cooldown_seconds=0
        ),
    )
    with pytest.raises(Conflict):
        raise_escalation(
            db,
            scope=scope,
            command=RaiseEscalation(
                policy_id=policy.id,
                subject_reference="outage:1",
                dedup_key="k",
                raised_at=NOW,
            ),
        )


def test_cancellation_stays_distinct_from_resolution(db: Session) -> None:
    """Collapsing the two loses the only signal that a policy is
    misconfigured: resolution says the condition ended, cancellation says the
    escalation should never have been raised."""
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    _active(db, scope, policy)
    resolved = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="k1",
            raised_at=NOW,
        ),
    )
    acknowledge_escalation(
        db,
        scope=scope,
        command=SettleEscalation(
            escalation_id=resolved.id, actor_reference="person:1", at=NOW
        ),
    )
    assert resolved.status is EscalationStatus.ACKNOWLEDGED
    resolve_escalation(
        db,
        scope=scope,
        command=SettleEscalation(
            escalation_id=resolved.id,
            actor_reference="person:1",
            reason="link restored",
            at=NOW,
        ),
    )
    assert resolved.status is EscalationStatus.RESOLVED

    cancelled = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:2",
            dedup_key="k2",
            raised_at=NOW,
        ),
    )
    cancel_escalation(
        db,
        scope=scope,
        command=SettleEscalation(
            escalation_id=cancelled.id,
            actor_reference="person:1",
            reason="threshold misconfigured",
            at=NOW,
        ),
    )
    assert cancelled.status is EscalationStatus.CANCELLED
    assert cancelled.settlement_reason == "threshold misconfigured"


def test_a_settled_escalation_cannot_be_acknowledged(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    policy = _policy(db, scope)
    _active(db, scope, policy)
    instance = raise_escalation(
        db,
        scope=scope,
        command=RaiseEscalation(
            policy_id=policy.id,
            subject_reference="outage:1",
            dedup_key="k1",
            raised_at=NOW,
        ),
    )
    resolve_escalation(
        db,
        scope=scope,
        command=SettleEscalation(
            escalation_id=instance.id, actor_reference="person:1", at=NOW
        ),
    )
    with pytest.raises(Conflict):
        acknowledge_escalation(
            db,
            scope=scope,
            command=SettleEscalation(
                escalation_id=instance.id, actor_reference="person:1", at=NOW
            ),
        )


def test_another_tenants_policy_is_not_visible(db: Session) -> None:
    policy = _policy(db, TenantScope(TENANT_A))
    with pytest.raises(Conflict):
        raise_escalation(
            db,
            scope=TenantScope(TENANT_B),
            command=RaiseEscalation(
                policy_id=policy.id,
                subject_reference="outage:1",
                dedup_key="k",
                raised_at=NOW,
            ),
        )
