"""Indexed shadow-comparison evidence belongs to the integration module.

The destination application owns the comparison.  This module owns only the
durable, provider-neutral evidence needed to decide whether a receipt should be
compared again and to aggregate one revision's latest outcomes.  Raw payloads,
provider identities, field values and exception text are deliberately
unrepresentable on the evidence row.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_integration import (
    RETRYABLE_SHADOW_VERDICTS,
    SHADOW_PLATFORM_TABLES,
    CapabilityBinding,
    ConnectorInstallation,
    InboxReceipt,
    SafeShadowVerdict,
    ShadowComparisonEvidence,
    due_shadow_receipt_ids,
    module,
    normalize_shadow_verdict,
    record_shadow_observation,
    shadow_report,
    unreadable_shadow_verdict,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
REVISION = "sub-team-inbox:7e05430:observation-v1"


@dataclass(frozen=True)
class _ProductVerdict:
    verdict: object
    agrees: object
    blocking_reasons: object
    disagreeing_fields: object


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        CapabilityBinding,
        InboxReceipt,
        ShadowComparisonEvidence,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def receipt(db: Session) -> InboxReceipt:
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="conformance_fake",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(installation)
    db.flush()
    binding = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id="message.observation.v1",
        state="enabled",
    )
    db.add(binding)
    db.flush()
    row = InboxReceipt(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="provider-owned-event-id",
        event_type="message.received",
        payload_digest="a" * 64,
        payload_json={"secret": "must-not-reach-shadow-evidence"},
        state="verified",
        received_at=NOW - timedelta(minutes=5),
    )
    db.add(row)
    db.flush()
    return row


def _verdict(
    verdict: str = "agrees",
    *,
    agrees: bool = True,
    reasons: tuple[str, ...] = (),
    fields: tuple[str, ...] = (),
) -> _ProductVerdict:
    return _ProductVerdict(verdict, agrees, reasons, fields)


def test_the_manifest_declares_the_shadow_table_on_the_platform_plane() -> None:
    assert SHADOW_PLATFORM_TABLES == ("shadow_comparison_evidence",)
    assert "shadow_comparison_evidence" in module.platform_tables
    assert "shadow_comparison_evidence" not in module.tables


def test_the_evidence_schema_cannot_hold_provider_material() -> None:
    columns = {column.name for column in ShadowComparisonEvidence.__table__.columns}
    assert columns == {
        "id",
        "receipt_id",
        "comparison_revision",
        "verdict",
        "blocking_reasons",
        "disagreeing_fields",
        "observed_at",
    }
    forbidden = {
        "payload",
        "headers",
        "provider_event_id",
        "provider",
        "exception",
        "error_detail",
    }
    assert columns.isdisjoint(forbidden)


def test_a_valid_destination_verdict_is_normalized_to_closed_evidence() -> None:
    result = normalize_shadow_verdict(
        _verdict(
            "field_disagreement",
            agrees=False,
            reasons=("normalized_field_disagreement",),
            fields=("normalized_payload.text", "observed_at"),
        )
    )
    assert result == SafeShadowVerdict(
        verdict="field_disagreement",
        blocking_reasons=("normalized_field_disagreement",),
        disagreeing_fields=("normalized_payload.text", "observed_at"),
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        _ProductVerdict("provider-secret-value", False, (), ()),
        _ProductVerdict("agrees", False, (), ()),
        _ProductVerdict("agrees", True, (), ("payload.hello world",)),
        _ProductVerdict("collision", False, ("raw exception text",), ()),
        _ProductVerdict("agrees", True, "not-an-array", ()),
    ],
)
def test_unsafe_destination_material_collapses_without_echo(
    unsafe: _ProductVerdict,
) -> None:
    result = normalize_shadow_verdict(unsafe)
    assert result == SafeShadowVerdict(
        verdict="unrecognized",
        blocking_reasons=("unrecognized_comparison_report",),
        disagreeing_fields=(),
    )
    rendered = repr(result)
    assert "provider-secret-value" not in rendered
    assert "raw exception text" not in rendered
    assert "hello world" not in rendered


def test_unreadable_evidence_carries_no_exception_text() -> None:
    assert unreadable_shadow_verdict() == SafeShadowVerdict(
        verdict="unreadable",
        blocking_reasons=("comparison_unreadable",),
        disagreeing_fields=(),
    )


def test_a_raising_destination_property_cannot_escape_material() -> None:
    material = "destination-property-secret-material"

    class _RaisingVerdict:
        @property
        def verdict(self) -> str:
            raise RuntimeError(material)

        agrees = False
        blocking_reasons: tuple[str, ...] = ()
        disagreeing_fields: tuple[str, ...] = ()

    result = normalize_shadow_verdict(_RaisingVerdict())
    assert result.verdict == "unrecognized"
    assert material not in repr(result)
    assert RETRYABLE_SHADOW_VERDICTS == frozenset(
        {"no_counterpart", "unreadable", "unrecognized"}
    )


def test_recording_is_append_only_and_flush_only(
    db: Session, receipt: InboxReceipt
) -> None:
    row = record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=normalize_shadow_verdict(_verdict()),
        observed_at=NOW,
    )
    assert row.id is not None
    assert db.in_transaction()
    stored = db.get(ShadowComparisonEvidence, row.id)
    assert stored is row
    assert stored.receipt_id == receipt.id
    assert stored.verdict == "agrees"
    assert stored.blocking_reasons == []
    assert stored.disagreeing_fields == []

    record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=unreadable_shadow_verdict(),
        observed_at=NOW + timedelta(seconds=1),
    )
    assert db.query(ShadowComparisonEvidence).count() == 2


def test_invalid_revision_is_refused_without_rendering_it(
    db: Session, receipt: InboxReceipt
) -> None:
    material = "revision with secret-looking whitespace"
    with pytest.raises(ValueError) as excinfo:
        record_shadow_observation(
            db,
            receipt_id=receipt.id,
            comparison_revision=material,
            verdict=unreadable_shadow_verdict(),
            observed_at=NOW,
        )
    assert material not in str(excinfo.value)
    assert db.query(ShadowComparisonEvidence).count() == 0


def test_terminal_evidence_is_compared_once_per_revision(
    db: Session, receipt: InboxReceipt
) -> None:
    assert due_shadow_receipt_ids(
        db, comparison_revision=REVISION, retry_after=timedelta(minutes=5), now=NOW
    ) == (receipt.id,)
    record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=normalize_shadow_verdict(_verdict()),
        observed_at=NOW,
    )
    assert (
        due_shadow_receipt_ids(
            db, comparison_revision=REVISION, retry_after=timedelta(minutes=5), now=NOW
        )
        == ()
    )
    assert due_shadow_receipt_ids(
        db,
        comparison_revision="new-image:observation-v1",
        retry_after=timedelta(minutes=5),
        now=NOW,
    ) == (receipt.id,)


def test_retryable_evidence_is_not_a_hot_loop(
    db: Session, receipt: InboxReceipt
) -> None:
    record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=unreadable_shadow_verdict(),
        observed_at=NOW,
    )
    assert (
        due_shadow_receipt_ids(
            db,
            comparison_revision=REVISION,
            retry_after=timedelta(minutes=5),
            now=NOW + timedelta(minutes=4, seconds=59),
        )
        == ()
    )
    assert due_shadow_receipt_ids(
        db,
        comparison_revision=REVISION,
        retry_after=timedelta(minutes=5),
        now=NOW + timedelta(minutes=5),
    ) == (receipt.id,)


def test_selector_never_claims_or_mutates_the_receipt(
    db: Session, receipt: InboxReceipt
) -> None:
    before = {
        column.name: getattr(receipt, column.name)
        for column in InboxReceipt.__table__.columns
    }
    due_shadow_receipt_ids(
        db, comparison_revision=REVISION, retry_after=timedelta(minutes=5), now=NOW
    )
    after = {
        column.name: getattr(receipt, column.name)
        for column in InboxReceipt.__table__.columns
    }
    assert after == before


def test_report_uses_only_the_latest_verdict_per_receipt(
    db: Session, receipt: InboxReceipt
) -> None:
    record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=unreadable_shadow_verdict(),
        observed_at=NOW,
    )
    record_shadow_observation(
        db,
        receipt_id=receipt.id,
        comparison_revision=REVISION,
        verdict=normalize_shadow_verdict(_verdict()),
        observed_at=NOW + timedelta(minutes=6),
    )
    report = shadow_report(db, comparison_revision=REVISION)
    assert report.unique_receipts == 1
    assert report.agreeing == 1
    assert report.verdict_counts == {"agrees": 1}
    assert report.blocking_reason_counts == {}
    assert report.disagreeing_fields == {}
    assert report.first_observed_at == NOW
    assert report.last_observed_at == NOW + timedelta(minutes=6)
    assert report.sample_has_no_blockers


def test_an_empty_report_is_never_cutover_evidence(db: Session) -> None:
    report = shadow_report(db, comparison_revision=REVISION)
    assert report.unique_receipts == 0
    assert not report.sample_has_no_blockers


def test_shadow_services_do_not_own_transactions_or_sessions() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "packages/dotmac-integration/src/dotmac_integration/shadow.py"
    ).read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "Session(" not in source
    assert "sessionmaker(" not in source
