"""Sub referral parity at the new tenant-only owner boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Base, Tenant
from dotmac_referrals import (
    CaptureReferral,
    ContractError,
    CreateProgramme,
    IssueCode,
    RecordConversion,
    capture_referral,
    create_programme,
    issue_code,
    record_conversion,
)
from dotmac_referrals.models import ALL_MODELS
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"public": None, "mod_referrals": None}
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


def test_capture_is_idempotent_and_does_not_resolve_product_identity(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    programme = create_programme(
        db,
        scope=scope,
        command=CreateProgramme(
            code="friend-2026",
            name="Friends 2026",
            qualification_policy_ref="sub:active-after-30-days:v1",
            reward_policy_ref="billing:credit:v2",
        ),
        recorded_at=NOW,
    )
    invitation = issue_code(
        db,
        scope=scope,
        command=IssueCode(
            programme_id=programme.id,
            referrer_ref="party:referrer-1",
            code=" Ada-2026 ",
            expires_at=NOW + timedelta(days=30),
        ),
        recorded_at=NOW,
    )
    command = CaptureReferral(
        code="ADA-2026",
        referred_subject_ref="prospect:opaque-1",
        source_owner="sub.signup",
        source_event_id="signup-1",
        source_fingerprint="a" * 64,
    )
    first = capture_referral(db, scope=scope, command=command, recorded_at=NOW)
    replay = capture_referral(db, scope=scope, command=command, recorded_at=NOW)

    assert invitation.code == "ADA-2026"
    assert first.id == replay.id
    assert first.referred_subject_ref == "prospect:opaque-1"
    assert not hasattr(first, "customer_id")
    assert not hasattr(first, "lead_id")


def test_conversion_preserves_evidence_and_emits_a_reward_request(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    programme = create_programme(
        db,
        scope=scope,
        command=CreateProgramme(
            code="convert",
            name="Conversion",
            qualification_policy_ref="sub:paid:v1",
            reward_policy_ref="billing:credit:v1",
        ),
        recorded_at=NOW,
    )
    issue_code(
        db,
        scope=scope,
        command=IssueCode(
            programme_id=programme.id,
            referrer_ref="party:referrer-1",
            code="CONVERT-1",
            expires_at=NOW + timedelta(days=10),
        ),
        recorded_at=NOW,
    )
    referral = capture_referral(
        db,
        scope=scope,
        command=CaptureReferral(
            code="CONVERT-1",
            referred_subject_ref="customer:opaque-7",
            source_owner="sub.customer",
            source_event_id="customer-7",
            source_fingerprint="b" * 64,
        ),
        recorded_at=NOW,
    )
    conversion = record_conversion(
        db,
        scope=scope,
        command=RecordConversion(
            referral_id=referral.id,
            conversion_ref="subscription:opaque-9",
            qualification_evidence_digest="c" * 64,
        ),
        recorded_at=NOW + timedelta(days=1),
    )

    outbox = db.scalar(select(OutboxEvent))
    assert conversion.conversion_ref == "subscription:opaque-9"
    assert conversion.reward_request_ref is not None
    assert outbox is not None
    assert outbox.event_type == "referrals.reward.requested.v1"
    assert outbox.payload["reward_policy_ref"] == "billing:credit:v1"


def test_contract_refuses_ambient_or_unbounded_identity() -> None:
    with pytest.raises(ContractError, match="source_fingerprint"):
        CaptureReferral(
            code="REF",
            referred_subject_ref="customer:1",
            source_owner="sub.signup",
            source_event_id="signup-1",
            source_fingerprint="not-a-digest",
        )
    with pytest.raises(ContractError, match="timezone-aware"):
        IssueCode(
            programme_id=uuid.uuid4(),
            referrer_ref="party:1",
            code="REF",
            expires_at=datetime(2026, 9, 1),
        )
