"""Flush-only outside-plant ledger and continuity owner."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_fiber_plant.contracts import (
    AcceptChange,
    ApproveChange,
    CableLookup,
    CableSnapshot,
    ChangeLookup,
    ChangeSnapshot,
    ChangeState,
    ContinuityPath,
    ContinuityQuery,
    FieldObservationSnapshot,
    ProposeChange,
    RecordFieldObservation,
    RecordSplice,
    RecordTermination,
    RegisterCable,
    RegisterStrand,
    RegisterStructure,
    StrandSnapshot,
    StrandState,
    StructureKind,
    StructureLookup,
    StructureSnapshot,
)
from dotmac_fiber_plant.models import (
    Cable,
    Change,
    FiberEvent,
    FieldObservation,
    Splice,
    Strand,
    Structure,
    Termination,
)


class FiberPlantError(ValueError):
    pass


class FiberPlantNotFound(FiberPlantError):
    pass


class FiberPlantConflict(FiberPlantError):
    pass


def _clean(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise FiberPlantError(f"{label} must not be blank")
    return result


def _structure(db: Session, tenant_id: UUID, structure_id: UUID) -> Structure:
    row = db.scalar(
        select(Structure).where(
            Structure.tenant_id == tenant_id, Structure.id == structure_id
        )
    )
    if row is None:
        raise FiberPlantNotFound("structure not found")
    return row


def _event(
    db: Session,
    tenant_id: UUID,
    ref: str,
    kind: str,
    evidence_ref: str,
    payload: dict[str, str],
    occurred_at: datetime | None = None,
) -> None:
    db.add(
        FiberEvent(
            tenant_id=tenant_id,
            aggregate_ref=ref,
            event_type=kind,
            evidence_ref=evidence_ref,
            payload=payload,
            occurred_at=occurred_at or datetime.now(UTC),
        )
    )


def _structure_snapshot(row: Structure) -> StructureSnapshot:
    return StructureSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        kind=StructureKind(row.kind),
        location_ref=row.location_ref,
        asset_ref=row.asset_ref,
        source_ref=row.source_ref,
        created_at=row.created_at,
    )


def _cable_snapshot(row: Cable) -> CableSnapshot:
    return CableSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        strand_count=row.strand_count,
        start_structure_id=row.start_structure_id,
        end_structure_id=row.end_structure_id,
        route_ref=row.route_ref,
        asset_ref=row.asset_ref,
        created_at=row.created_at,
    )


def _strand_snapshot(row: Strand) -> StrandSnapshot:
    return StrandSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        cable_id=row.cable_id,
        ordinal=row.ordinal,
        colour_code=row.colour_code,
        state=StrandState(row.state),
        created_at=row.created_at,
    )


def _change_snapshot(row: Change) -> ChangeSnapshot:
    return ChangeSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        summary=row.summary,
        subject_refs=tuple(row.subject_refs),
        state=ChangeState(row.state),
        desired_fingerprint=row.desired_fingerprint,
        as_built_fingerprint=row.as_built_fingerprint,
        requested_by_ref=row.requested_by_ref,
        approval_ref=row.approval_ref,
        accepted_at=row.accepted_at,
    )


def register_structure(
    db: Session, *, tenant_id: UUID, command: RegisterStructure
) -> StructureSnapshot:
    row = Structure(
        tenant_id=tenant_id,
        code=_clean(command.code, "structure code"),
        name=_clean(command.name, "structure name"),
        kind=command.kind.value,
        location_ref=_clean(command.location_ref, "location reference"),
        asset_ref=command.asset_ref,
        source_ref=command.source_ref,
        created_at=datetime.now(UTC),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"structure:{row.id}",
                "structure_registered",
                command.source_ref or row.code,
                {"kind": row.kind},
            )
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("structure code already exists") from exc
    return _structure_snapshot(row)


def register_cable(
    db: Session, *, tenant_id: UUID, command: RegisterCable
) -> CableSnapshot:
    if (
        command.start_structure_id == command.end_structure_id
        or command.strand_count <= 0
    ):
        raise FiberPlantError(
            "cable needs distinct endpoints and positive strand count"
        )
    _structure(db, tenant_id, command.start_structure_id)
    _structure(db, tenant_id, command.end_structure_id)
    row = Cable(
        tenant_id=tenant_id,
        code=_clean(command.code, "cable code"),
        name=_clean(command.name, "cable name"),
        strand_count=command.strand_count,
        start_structure_id=command.start_structure_id,
        end_structure_id=command.end_structure_id,
        route_ref=command.route_ref,
        asset_ref=command.asset_ref,
        created_at=datetime.now(UTC),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("cable code already exists") from exc
    return _cable_snapshot(row)


def register_strand(
    db: Session, *, tenant_id: UUID, command: RegisterStrand
) -> StrandSnapshot:
    cable = db.scalar(
        select(Cable).where(Cable.tenant_id == tenant_id, Cable.id == command.cable_id)
    )
    if cable is None:
        raise FiberPlantNotFound("cable not found")
    if not 1 <= command.ordinal <= cable.strand_count:
        raise FiberPlantError("strand ordinal exceeds cable capacity")
    row = Strand(
        tenant_id=tenant_id,
        cable_id=cable.id,
        ordinal=command.ordinal,
        colour_code=_clean(command.colour_code, "strand colour"),
        state=StrandState.AVAILABLE.value,
        created_at=datetime.now(UTC),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("strand ordinal already exists") from exc
    return _strand_snapshot(row)


def record_splice(db: Session, *, tenant_id: UUID, command: RecordSplice) -> UUID:
    if command.left_strand_id == command.right_strand_id:
        raise FiberPlantError("a strand cannot splice to itself")
    _structure(db, tenant_id, command.structure_id)
    strands = tuple(
        db.scalars(
            select(Strand)
            .where(
                Strand.tenant_id == tenant_id,
                Strand.id.in_((command.left_strand_id, command.right_strand_id)),
            )
            .with_for_update()
        )
    )
    if len(strands) != 2:
        raise FiberPlantNotFound("one or more strands not found")
    cables = {
        row.id: row
        for row in db.scalars(
            select(Cable).where(
                Cable.tenant_id == tenant_id,
                Cable.id.in_({strand.cable_id for strand in strands}),
            )
        )
    }
    if any(
        command.structure_id
        not in {
            cables[strand.cable_id].start_structure_id,
            cables[strand.cable_id].end_structure_id,
        }
        for strand in strands
    ):
        raise FiberPlantConflict("splice structure must terminate both source cables")
    left_strand_id, right_strand_id = sorted(
        (command.left_strand_id, command.right_strand_id)
    )
    row = Splice(
        tenant_id=tenant_id,
        structure_id=command.structure_id,
        left_strand_id=left_strand_id,
        right_strand_id=right_strand_id,
        loss_db=command.loss_db,
        evidence_ref=_clean(command.evidence_ref, "evidence reference"),
        occurred_at=command.occurred_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            for strand in strands:
                strand.state = StrandState.CONNECTED.value
            db.flush()
            _event(
                db,
                tenant_id,
                f"splice:{row.id}",
                "splice_recorded",
                row.evidence_ref,
                {"left": str(row.left_strand_id), "right": str(row.right_strand_id)},
                command.occurred_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("splice pair already exists") from exc
    return row.id


def record_termination(
    db: Session, *, tenant_id: UUID, command: RecordTermination
) -> UUID:
    _structure(db, tenant_id, command.structure_id)
    strand = db.scalar(
        select(Strand)
        .where(Strand.tenant_id == tenant_id, Strand.id == command.strand_id)
        .with_for_update()
    )
    if strand is None:
        raise FiberPlantNotFound("strand not found")
    cable = db.scalar(
        select(Cable).where(Cable.tenant_id == tenant_id, Cable.id == strand.cable_id)
    )
    if cable is None:
        raise FiberPlantNotFound("strand cable not found")
    if command.structure_id not in {
        cable.start_structure_id,
        cable.end_structure_id,
    }:
        raise FiberPlantConflict("termination structure is not a cable endpoint")
    row = Termination(
        tenant_id=tenant_id,
        structure_id=command.structure_id,
        strand_id=strand.id,
        endpoint_ref=_clean(command.endpoint_ref, "endpoint reference"),
        port_ref=command.port_ref,
        evidence_ref=_clean(command.evidence_ref, "evidence reference"),
        occurred_at=command.occurred_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            strand.state = StrandState.CONNECTED.value
            db.flush()
            _event(
                db,
                tenant_id,
                f"termination:{row.id}",
                "termination_recorded",
                row.evidence_ref,
                {"strand_id": str(row.strand_id)},
                command.occurred_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("strand termination already exists") from exc
    return row.id


def record_field_observation(
    db: Session, *, tenant_id: UUID, command: RecordFieldObservation
) -> FieldObservationSnapshot:
    row = FieldObservation(
        tenant_id=tenant_id,
        subject_ref=_clean(command.subject_ref, "subject reference"),
        observation_kind=_clean(command.observation_kind, "observation kind"),
        result_code=_clean(command.result_code, "result code"),
        evidence_ref=_clean(command.evidence_ref, "evidence reference"),
        observed_at=command.observed_at,
        actor_ref=command.actor_ref,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("field evidence already exists") from exc
    return FieldObservationSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_ref=row.subject_ref,
        observation_kind=row.observation_kind,
        result_code=row.result_code,
        evidence_ref=row.evidence_ref,
        observed_at=row.observed_at,
        actor_ref=row.actor_ref,
    )


def propose_change(
    db: Session, *, tenant_id: UUID, command: ProposeChange
) -> ChangeSnapshot:
    row = Change(
        tenant_id=tenant_id,
        code=_clean(command.code, "change code"),
        summary=_clean(command.summary, "change summary"),
        subject_refs=list(command.subject_refs),
        state=ChangeState.PROPOSED.value,
        desired_fingerprint=_clean(command.desired_fingerprint, "desired fingerprint"),
        requested_by_ref=_clean(command.requested_by_ref, "requester reference"),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise FiberPlantConflict("change code already exists") from exc
    return _change_snapshot(row)


def approve_change(
    db: Session, *, tenant_id: UUID, command: ApproveChange
) -> ChangeSnapshot:
    row = db.scalar(
        select(Change)
        .where(Change.tenant_id == tenant_id, Change.id == command.change_id)
        .with_for_update()
    )
    if row is None:
        raise FiberPlantNotFound("change not found")
    if (
        ChangeState(row.state) is not command.expected
        or command.expected is not ChangeState.PROPOSED
    ):
        raise FiberPlantConflict("change approval state changed")
    row.state = ChangeState.APPROVED.value
    row.approval_ref = _clean(command.approval_ref, "approval reference")
    _event(
        db,
        tenant_id,
        f"change:{row.id}",
        "change_approved",
        row.approval_ref,
        {"approved_by_ref": command.approved_by_ref},
        command.approved_at,
    )
    db.flush()
    return _change_snapshot(row)


def accept_change(
    db: Session, *, tenant_id: UUID, command: AcceptChange
) -> ChangeSnapshot:
    row = db.scalar(
        select(Change)
        .where(Change.tenant_id == tenant_id, Change.id == command.change_id)
        .with_for_update()
    )
    if row is None:
        raise FiberPlantNotFound("change not found")
    if (
        ChangeState(row.state) is not command.expected
        or command.expected is not ChangeState.APPROVED
    ):
        raise FiberPlantConflict("change acceptance state changed")
    if not command.evidence_refs:
        raise FiberPlantError("accepted change requires evidence")
    row.state = ChangeState.ACCEPTED.value
    row.as_built_fingerprint = _clean(
        command.as_built_fingerprint, "as-built fingerprint"
    )
    row.accepted_at = command.accepted_at
    _event(
        db,
        tenant_id,
        f"change:{row.id}",
        "fiber_change_accepted",
        command.evidence_refs[0],
        {"accepted_by_ref": command.accepted_by_ref},
        command.accepted_at,
    )
    db.flush()
    return _change_snapshot(row)


def resolve_continuity(
    db: Session, *, tenant_id: UUID, query: ContinuityQuery
) -> ContinuityPath:
    terminations = tuple(
        db.scalars(select(Termination).where(Termination.tenant_id == tenant_id))
    )
    splices = tuple(db.scalars(select(Splice).where(Splice.tenant_id == tenant_id)))
    starts = [
        row.strand_id for row in terminations if row.endpoint_ref == query.from_ref
    ]
    targets = {
        row.strand_id for row in terminations if row.endpoint_ref == query.to_ref
    }
    graph: dict[UUID, list[tuple[UUID, UUID]]] = {}
    for splice in splices:
        graph.setdefault(splice.left_strand_id, []).append(
            (splice.right_strand_id, splice.id)
        )
        graph.setdefault(splice.right_strand_id, []).append(
            (splice.left_strand_id, splice.id)
        )
    queue: deque[tuple[UUID, tuple[UUID, ...], tuple[UUID, ...]]] = deque(
        (strand, (strand,), ()) for strand in starts
    )
    visited = set(starts)
    while queue:
        strand, strand_ids, splice_ids = queue.popleft()
        if strand in targets:
            return ContinuityPath(
                tenant_id=tenant_id,
                from_ref=query.from_ref,
                to_ref=query.to_ref,
                strand_ids=strand_ids,
                splice_ids=splice_ids,
                continuous=True,
                reason_code=None,
                as_of=query.as_of or datetime.now(UTC),
            )
        for next_strand, splice_id in graph.get(strand, []):
            if next_strand not in visited:
                visited.add(next_strand)
                queue.append(
                    (next_strand, (*strand_ids, next_strand), (*splice_ids, splice_id))
                )
    return ContinuityPath(
        tenant_id=tenant_id,
        from_ref=query.from_ref,
        to_ref=query.to_ref,
        strand_ids=(),
        splice_ids=(),
        continuous=False,
        reason_code="no-continuous-path",
        as_of=query.as_of or datetime.now(UTC),
    )


def lookup_structures(
    db: Session, *, tenant_id: UUID, query: StructureLookup
) -> tuple[StructureSnapshot, ...]:
    statement = select(Structure).where(Structure.tenant_id == tenant_id)
    if query.structure_id is not None:
        statement = statement.where(Structure.id == query.structure_id)
    if query.code is not None:
        statement = statement.where(Structure.code == query.code)
    return tuple(_structure_snapshot(row) for row in db.scalars(statement))


def lookup_cables(
    db: Session, *, tenant_id: UUID, query: CableLookup
) -> tuple[CableSnapshot, ...]:
    statement = select(Cable).where(Cable.tenant_id == tenant_id)
    if query.cable_id is not None:
        statement = statement.where(Cable.id == query.cable_id)
    if query.structure_id is not None:
        statement = statement.where(
            or_(
                Cable.start_structure_id == query.structure_id,
                Cable.end_structure_id == query.structure_id,
            )
        )
    return tuple(_cable_snapshot(row) for row in db.scalars(statement))


def lookup_changes(
    db: Session, *, tenant_id: UUID, query: ChangeLookup
) -> tuple[ChangeSnapshot, ...]:
    statement = select(Change).where(Change.tenant_id == tenant_id)
    if query.change_id is not None:
        statement = statement.where(Change.id == query.change_id)
    if query.code is not None:
        statement = statement.where(Change.code == query.code)
    return tuple(_change_snapshot(row) for row in db.scalars(statement))


__all__ = [
    "FiberPlantConflict",
    "FiberPlantError",
    "FiberPlantNotFound",
    "accept_change",
    "approve_change",
    "lookup_cables",
    "lookup_changes",
    "lookup_structures",
    "propose_change",
    "record_field_observation",
    "record_splice",
    "record_termination",
    "register_cable",
    "register_strand",
    "register_structure",
    "resolve_continuity",
]
