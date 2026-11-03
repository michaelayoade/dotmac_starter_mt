"""X1 conformance: an UNKNOWN due date stays fail-closed end to end.

X1 is the Billing -> assembly -> Collections receivable seam.  Billing
publishes a ``ReceivableExposureV1`` whose due-date basis is
``unknown_unverified``; the consuming assembly maps it to Collections'
``ReceivableObservationV1``.  Collections must never read an unknown due date
as "due now" or as "not due": it blocks automated collection, writes no case,
schedules no timer and requests no notice or action, until Billing publishes a
verified due date.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from dotmac_billing.contracts import (
    DueDateBasisStatus,
    DueDateBasisV1,
    ReceivableExposureV1,
)
from dotmac_collections.models import (
    CollectionActionRequest,
    CollectionCase,
    CollectionCaseExposure,
    CollectionNoticeRequest,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionReadOk,
    ReceivableObservationV1,
)
from dotmac_collections.service import (
    AssessmentBlocked,
    CaseAssessed,
    CollectionCaseService,
)
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging.models import OutboxEvent
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.architecture.test_receivable_contract_ownership import (
    _map_for_collections,
    _unknown_due_exposure,
)
from tests.unit import test_collections_service_contract as base


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_coll": None}},
    )
    IdempotencyRecord.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    for model in base.COLLECTION_TABLES:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _aligned(exposure: ReceivableExposureV1) -> ReceivableExposureV1:
    """Align the Billing fixture with the identity the Collections command uses."""

    return replace(
        exposure,
        scope=base.SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        subject_ref="subscriber:sub-1",
        service_ref="service:svc-1",
        observed_at=base.NOW,
    )


def _reader(observation: ReceivableObservationV1) -> FakeReceivablesReader:
    return base._reader(PositionReadOk(observation))


def _row_counts(db: Session) -> dict[str, int]:
    return {
        model.__name__: len(db.scalars(select(model)).all())
        for model in (
            CollectionCase,
            CollectionCaseExposure,
            CollectionNoticeRequest,
            CollectionActionRequest,
            OutboxEvent,
        )
    }


def test_unknown_due_date_exposure_maps_to_an_unverified_observation() -> None:
    exposure = _aligned(_unknown_due_exposure(observed_at=base.NOW))
    assert exposure.due_at is None
    observation = _map_for_collections(exposure, reason_code="invoice_overdue")
    assert observation.due_at is None
    assert observation.due_date_status == "unknown_unverified"
    assert observation.automated_collection_blocker(as_of=base.NOW) == (
        "due_date_unverified"
    )
    # Not read as "overdue", and not read as "not due" either.
    assert (
        observation.automated_collection_blocker(as_of=base.NOW + timedelta(days=3650))
        == "due_date_unverified"
    )


def test_unknown_due_date_assessment_blocks_and_writes_nothing(db: Session) -> None:
    policy_version_id = base._policy(db)
    exposure = _aligned(_unknown_due_exposure(observed_at=base.NOW))
    observation = _map_for_collections(exposure, reason_code="invoice_overdue")
    before = _row_counts(db)

    result = CollectionCaseService.assess(
        db,
        command=base._command(),
        policy_version_id=policy_version_id,
        reader=_reader(observation),
        assessed_at=base.NOW,
    )

    assert result == AssessmentBlocked("due_date_unverified", None)
    assert _row_counts(db) == before
    assert _row_counts(db)["CollectionCase"] == 0
    assert _row_counts(db)["OutboxEvent"] == 0


def test_unknown_due_date_stays_blocked_on_replay_until_billing_verifies(
    db: Session,
) -> None:
    policy_version_id = base._policy(db)
    unknown = _map_for_collections(
        _aligned(_unknown_due_exposure(observed_at=base.NOW)),
        reason_code="invoice_overdue",
    )
    for _ in range(2):
        result = CollectionCaseService.assess(
            db,
            command=base._command(),
            policy_version_id=policy_version_id,
            reader=_reader(unknown),
            assessed_at=base.NOW,
        )
        assert result == AssessmentBlocked("due_date_unverified", None)
    assert db.scalars(select(CollectionCase)).all() == []

    verified_exposure = replace(
        _aligned(_unknown_due_exposure(observed_at=base.NOW)),
        source_version=2,
        state_fingerprint="e" * 64,
        due_at=base.NOW - timedelta(days=1),
        due_date_basis=DueDateBasisV1(
            status=DueDateBasisStatus.VERIFIED,
            source_authority="dotmac-billing",
            evidence_ref="invoice:inv-1:terms",
            payment_terms_code="net_7",
            payment_terms_version="1",
            issued_at=base.NOW - timedelta(days=8),
            effective_at=base.NOW - timedelta(days=8),
            timezone="UTC",
            derivation_policy="issue_date_plus_terms",
            derivation_version="1",
        ),
    )
    verified = _map_for_collections(verified_exposure, reason_code="invoice_overdue")
    assert verified.due_date_status == "verified"

    opened = CollectionCaseService.assess(
        db,
        command=base._command(),
        policy_version_id=policy_version_id,
        reader=_reader(verified),
        assessed_at=base.NOW,
    )
    assert isinstance(opened, CaseAssessed)
    assert len(db.scalars(select(CollectionCase)).all()) == 1


def test_unverified_due_date_cannot_be_forced_through_a_due_at() -> None:
    exposure = _aligned(_unknown_due_exposure(observed_at=base.NOW))
    observation = _map_for_collections(exposure, reason_code="invoice_overdue")
    with pytest.raises(ValueError, match="unverified due date"):
        replace(observation, due_at=base.NOW - timedelta(days=30))
