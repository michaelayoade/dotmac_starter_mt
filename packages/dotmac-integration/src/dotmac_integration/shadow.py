"""Indexed, revisioned evidence for a product-owned shadow comparison.

The destination application owns the comparison and its domain reads.  This
module owns only scheduling and durable evidence: which local receipt was
compared, against which deployment revision, and the closed privacy-safe
outcome.  It never receives a product session and cannot represent provider
identities, payloads, headers, field values or exception text.

Services accept a caller-owned :class:`sqlalchemy.orm.Session`, mutate and
flush.  Transaction boundaries remain with the independently deployed
Integrator assembly, like every other service in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql.selectable import Subquery

from dotmac_integration.models import SCHEMA, InboxReceipt

SHADOW_PLATFORM_TABLES: tuple[str, ...] = ("shadow_comparison_evidence",)

RETRYABLE_SHADOW_VERDICTS = frozenset({"no_counterpart", "unreadable", "unrecognized"})

_EXPECTED_BLOCKERS: dict[str, frozenset[frozenset[str]]] = {
    "agrees": frozenset({frozenset()}),
    "field_disagreement": frozenset({frozenset({"normalized_field_disagreement"})}),
    "identity_shape_mismatch": frozenset(
        {
            frozenset({"identity_shape_mismatch"}),
            frozenset({"identity_shape_mismatch", "normalized_field_disagreement"}),
        }
    ),
    "collision": frozenset({frozenset({"domain_fingerprint_collision"})}),
    "no_counterpart": frozenset({frozenset({"no_counterpart_observation"})}),
    "unreadable": frozenset({frozenset({"comparison_unreadable"})}),
    "unrecognized": frozenset({frozenset({"unrecognized_comparison_report"})}),
}
_SAFE_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,119}$")
_SAFE_REVISION = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,159}$")
_UNRECOGNIZED_REASON = "unrecognized_comparison_report"
_DELIVERABLE_STATES = ("verified", "retryable")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class _MirrorVerdict(Protocol):
    verdict: object
    agrees: object
    blocking_reasons: object
    disagreeing_fields: object


class ShadowEvidenceCorrupt(RuntimeError):
    """Stored evidence cannot safely be used in an operator report."""


class ShadowComparisonEvidence(Base):
    """One append-only, privacy-safe comparison observation."""

    __tablename__ = "shadow_comparison_evidence"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('agrees', 'field_disagreement', "
            "'identity_shape_mismatch', 'collision', 'no_counterpart', "
            "'unreadable', 'unrecognized')",
            name="ck_shadow_comparison_evidence_verdict",
        ),
        Index(
            "ix_shadow_evidence_revision_receipt_latest",
            "comparison_revision",
            "receipt_id",
            "observed_at",
            "id",
        ),
        Index(
            "ix_shadow_evidence_revision_observed",
            "comparison_revision",
            "observed_at",
        ),
        schema_table_args(SCHEMA),
    )

    # An insertion-ordered key makes the latest row deterministic even when a
    # database rounds two observation timestamps to the same instant.
    id: Mapped[int] = mapped_column(
        sa.BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    receipt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        ForeignKey(f"{SCHEMA}.inbox_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    comparison_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False)
    blocking_reasons: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    disagreeing_fields: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _utc(value: datetime) -> datetime:
    return (
        value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    ).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SafeShadowVerdict:
    """The complete destination output permitted to reach persistence."""

    verdict: str
    blocking_reasons: tuple[str, ...]
    disagreeing_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        reasons = tuple(sorted(set(self.blocking_reasons)))
        fields = tuple(sorted(set(self.disagreeing_fields)))
        expected = _EXPECTED_BLOCKERS.get(self.verdict)
        if (
            expected is None
            or self.blocking_reasons != reasons
            or self.disagreeing_fields != fields
            or frozenset(reasons) not in expected
            or any(_SAFE_FIELD_NAME.fullmatch(field) is None for field in fields)
            or (self.verdict == "agrees" and fields)
        ):
            raise ValueError("shadow verdict is not safe durable evidence")

    @property
    def retryable(self) -> bool:
        return self.verdict in RETRYABLE_SHADOW_VERDICTS


def _unrecognized_verdict() -> SafeShadowVerdict:
    return SafeShadowVerdict(
        verdict="unrecognized",
        blocking_reasons=(_UNRECOGNIZED_REASON,),
        disagreeing_fields=(),
    )


def normalize_shadow_verdict(value: _MirrorVerdict) -> SafeShadowVerdict:
    """Validate a destination report and discard every unsafe string.

    Malformed output becomes one closed ``unrecognized`` result.  Rejected
    values and exceptions are never interpolated or chained: this is the final
    boundary before persistence, so a buggy destination cannot turn the
    evidence table or an operator traceback into a material sink.
    """

    try:
        verdict = value.verdict
        agrees = value.agrees
        raw_reasons = value.blocking_reasons
        raw_fields = value.disagreeing_fields
        if not isinstance(verdict, str) or not isinstance(agrees, bool):
            return _unrecognized_verdict()
        if not isinstance(raw_reasons, tuple | list) or not isinstance(
            raw_fields, tuple | list
        ):
            return _unrecognized_verdict()
        if not all(isinstance(item, str) for item in (*raw_reasons, *raw_fields)):
            return _unrecognized_verdict()
        reasons = tuple(sorted(set(raw_reasons)))
        fields = tuple(sorted(set(raw_fields)))
        if agrees is not (verdict == "agrees"):
            return _unrecognized_verdict()
        return SafeShadowVerdict(
            verdict=verdict,
            blocking_reasons=reasons,
            disagreeing_fields=fields,
        )
    # A property-backed implementation can raise any exception while exposing
    # its fields. This boundary deliberately preserves no diagnostic text from
    # destination-owned code; the closed outcome is the diagnostic.
    except Exception:
        return _unrecognized_verdict()


def unreadable_shadow_verdict() -> SafeShadowVerdict:
    """A transport failure with no exception text retained."""

    return SafeShadowVerdict(
        verdict="unreadable",
        blocking_reasons=("comparison_unreadable",),
        disagreeing_fields=(),
    )


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    id: int
    receipt_id: UUID
    comparison_revision: str
    verdict: str
    blocking_reasons: tuple[str, ...]
    disagreeing_fields: tuple[str, ...]
    observed_at: datetime

    @property
    def retryable(self) -> bool:
        return self.verdict in RETRYABLE_SHADOW_VERDICTS


@dataclass(frozen=True, slots=True)
class ShadowReport:
    """Latest outcome per receipt for one revision, without row identities."""

    comparison_revision: str
    unique_receipts: int
    agreeing: int
    verdict_counts: dict[str, int]
    blocking_reason_counts: dict[str, int]
    disagreeing_fields: dict[str, int]
    first_observed_at: datetime | None
    last_observed_at: datetime | None

    @property
    def sample_has_no_blockers(self) -> bool:
        # Deliberately not named is_cutover_safe. A full cutover also needs a
        # complete traffic cycle, collision/replay proof and rollback owner.
        return self.unique_receipts > 0 and not self.blocking_reason_counts

    def as_dict(self) -> dict[str, object]:
        return {
            "comparison_revision": self.comparison_revision,
            "unique_receipts": self.unique_receipts,
            "agreeing": self.agreeing,
            "verdict_counts": dict(sorted(self.verdict_counts.items())),
            "blocking_reason_counts": dict(sorted(self.blocking_reason_counts.items())),
            "disagreeing_fields": dict(sorted(self.disagreeing_fields.items())),
            "first_observed_at": (
                self.first_observed_at.isoformat()
                if self.first_observed_at is not None
                else None
            ),
            "last_observed_at": (
                self.last_observed_at.isoformat()
                if self.last_observed_at is not None
                else None
            ),
            "sample_has_no_blockers": self.sample_has_no_blockers,
        }


def _require_revision(value: object) -> str:
    if not isinstance(value, str) or _SAFE_REVISION.fullmatch(value) is None:
        raise ValueError("comparison revision is invalid")
    return value


def record_shadow_observation(
    db: Session,
    *,
    receipt_id: UUID,
    comparison_revision: str,
    verdict: SafeShadowVerdict,
    observed_at: datetime | None = None,
) -> ShadowComparisonEvidence:
    """Append one observation and flush; the caller owns commit/rollback."""

    revision = _require_revision(comparison_revision)
    row = ShadowComparisonEvidence(
        receipt_id=receipt_id,
        comparison_revision=revision,
        verdict=verdict.verdict,
        blocking_reasons=list(verdict.blocking_reasons),
        disagreeing_fields=list(verdict.disagreeing_fields),
        **({"observed_at": _utc(observed_at)} if observed_at is not None else {}),
    )
    db.add(row)
    db.flush()
    return row


def _latest_shadow_evidence(comparison_revision: str) -> Subquery:
    evidence = ShadowComparisonEvidence
    ranked = (
        sa.select(
            evidence.id,
            evidence.receipt_id,
            evidence.verdict,
            evidence.blocking_reasons,
            evidence.disagreeing_fields,
            evidence.observed_at,
            sa.func.row_number()
            .over(
                partition_by=evidence.receipt_id,
                order_by=(evidence.observed_at.desc(), evidence.id.desc()),
            )
            .label("position"),
        )
        .where(evidence.comparison_revision == comparison_revision)
        .subquery("ranked_shadow_evidence")
    )
    return (
        sa.select(
            ranked.c.id,
            ranked.c.receipt_id,
            ranked.c.verdict,
            ranked.c.blocking_reasons,
            ranked.c.disagreeing_fields,
            ranked.c.observed_at,
        )
        .where(ranked.c.position == 1)
        .subquery("latest_shadow_evidence")
    )


def due_shadow_receipt_ids(
    db: Session,
    *,
    comparison_revision: str,
    retry_after: timedelta,
    now: datetime,
    limit: int = 100,
) -> tuple[UUID, ...]:
    """Select receipts needing comparison without claiming or mutating them."""

    revision = _require_revision(comparison_revision)
    if retry_after < timedelta(0):
        raise ValueError("shadow retry interval cannot be negative")
    if limit < 1 or limit > 10_000:
        raise ValueError("shadow selection limit must be between 1 and 10000")
    current = _utc(now)
    retry_before = current - retry_after
    latest = _latest_shadow_evidence(revision)
    receipt = InboxReceipt
    rows = db.execute(
        sa.select(receipt.id)
        .outerjoin(latest, receipt.id == latest.c.receipt_id)
        .where(
            receipt.state.in_(_DELIVERABLE_STATES),
            sa.or_(receipt.leased_until.is_(None), receipt.leased_until < current),
            sa.or_(
                receipt.next_attempt_at.is_(None), receipt.next_attempt_at <= current
            ),
            sa.or_(
                latest.c.receipt_id.is_(None),
                sa.and_(
                    latest.c.verdict.in_(tuple(RETRYABLE_SHADOW_VERDICTS)),
                    latest.c.observed_at <= retry_before,
                ),
            ),
        )
        .order_by(receipt.received_at, receipt.id)
        .limit(limit)
    ).all()
    return tuple(row[0] for row in rows)


def _observation_from_latest(
    row: object, comparison_revision: str
) -> ShadowObservation:
    try:
        mapping = row._mapping  # type: ignore[attr-defined]
        reasons = mapping["blocking_reasons"]
        fields = mapping["disagreeing_fields"]
        if not isinstance(reasons, list) or not isinstance(fields, list):
            raise TypeError
        safe = SafeShadowVerdict(
            verdict=mapping["verdict"],
            blocking_reasons=tuple(reasons),
            disagreeing_fields=tuple(fields),
        )
        observed_at = mapping["observed_at"]
        if not isinstance(observed_at, datetime):
            raise TypeError
        return ShadowObservation(
            id=mapping["id"],
            receipt_id=mapping["receipt_id"],
            comparison_revision=comparison_revision,
            verdict=safe.verdict,
            blocking_reasons=safe.blocking_reasons,
            disagreeing_fields=safe.disagreeing_fields,
            observed_at=_utc(observed_at),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ShadowEvidenceCorrupt(
            "shadow evidence is malformed; refusing to produce a report"
        ) from exc


def shadow_report(db: Session, *, comparison_revision: str) -> ShadowReport:
    """Aggregate the latest safe outcome per receipt for one revision."""

    revision = _require_revision(comparison_revision)
    latest = _latest_shadow_evidence(revision)
    observations = tuple(
        _observation_from_latest(row, revision)
        for row in db.execute(sa.select(latest).order_by(latest.c.receipt_id)).all()
    )
    verdict_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    for item in observations:
        verdict_counts[item.verdict] = verdict_counts.get(item.verdict, 0) + 1
        for reason in item.blocking_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for field in item.disagreeing_fields:
            field_counts[field] = field_counts.get(field, 0) + 1

    bounds = db.execute(
        sa.select(
            sa.func.min(ShadowComparisonEvidence.observed_at),
            sa.func.max(ShadowComparisonEvidence.observed_at),
        ).where(ShadowComparisonEvidence.comparison_revision == revision)
    ).one()
    first = _utc(bounds[0]) if bounds[0] is not None else None
    last = _utc(bounds[1]) if bounds[1] is not None else None
    return ShadowReport(
        comparison_revision=revision,
        unique_receipts=len(observations),
        agreeing=verdict_counts.get("agrees", 0),
        verdict_counts=dict(sorted(verdict_counts.items())),
        blocking_reason_counts=dict(sorted(reason_counts.items())),
        disagreeing_fields=dict(sorted(field_counts.items())),
        first_observed_at=first,
        last_observed_at=last,
    )


__all__ = [
    "RETRYABLE_SHADOW_VERDICTS",
    "SHADOW_PLATFORM_TABLES",
    "SafeShadowVerdict",
    "ShadowComparisonEvidence",
    "ShadowEvidenceCorrupt",
    "ShadowObservation",
    "ShadowReport",
    "due_shadow_receipt_ids",
    "normalize_shadow_verdict",
    "record_shadow_observation",
    "shadow_report",
    "unreadable_shadow_verdict",
]
