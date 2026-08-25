"""Digital Media decisions over an assembly-owned SQLAlchemy session.

Services mutate and flush only. Authentication, transaction commit/rollback,
provider execution and sibling-module calls remain with the adopting assembly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from dotmac_kernel.idempotency import fingerprint_of
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from dotmac_digital_media.contracts import (
    AccessDecision,
    AccessGrantCommand,
    AccessScope,
    AssetLifecycle,
    Conflict,
    CreateAsset,
    CreateLibrary,
    DispositionDecision,
    DuplicateCandidate,
    GrantEffect,
    InvalidEvidence,
    LifecycleError,
    MetadataObservationCommand,
    NotFound,
    Permission,
    RecordDispositionObservation,
    RenditionCommand,
    RenditionOutput,
    RenditionState,
    RevisionCommand,
    RightsCommand,
    RightsDeadlineObservation,
    RightsDeadlinePurpose,
    RightsDecision,
    RightsError,
    RightsUse,
    ScanObservation,
    UsageObservationCommand,
)
from dotmac_digital_media.models import (
    MediaAccessGrant,
    MediaAnnotation,
    MediaAsset,
    MediaClassificationAssignment,
    MediaCollection,
    MediaCollectionItem,
    MediaEvent,
    MediaLibrary,
    MediaMetadataObservation,
    MediaRelationship,
    MediaRendition,
    MediaRevision,
    MediaRightsVersion,
    MediaSavedSelection,
    MediaUsageObservation,
)


def _required(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidEvidence(f"{field} is required")
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _asset(
    db: Session, tenant_id: UUID, asset_id: UUID, *, for_update: bool = False
) -> MediaAsset:
    statement = select(MediaAsset).where(
        MediaAsset.tenant_id == tenant_id, MediaAsset.id == asset_id
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise NotFound("media asset not found")
    return row


def _revision(db: Session, tenant_id: UUID, revision_id: UUID) -> MediaRevision:
    row = db.scalar(
        select(MediaRevision).where(
            MediaRevision.tenant_id == tenant_id, MediaRevision.id == revision_id
        )
    )
    if row is None:
        raise NotFound("media revision not found")
    return row


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    identity: str,
    actor_ref: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
) -> MediaEvent:
    row = MediaEvent(
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        event_identity=_required(identity, field="event identity"),
        actor_ref=_required(actor_ref, field="actor_ref"),
        occurred_at=occurred_at,
        payload=dict(payload),
    )
    db.add(row)
    db.flush()
    return row


def create_library(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateLibrary,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaLibrary:
    code = _required(command.code, field="library code").lower()
    row = MediaLibrary(
        tenant_id=tenant_id,
        code=code,
        name=_required(command.name, field="library name"),
        description=_optional(command.description),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="library",
        aggregate_id=row.id,
        event_type="library_created",
        identity=f"library:{row.id}:created",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={"code": code},
    )
    return row


def create_asset(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateAsset,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaAsset:
    library = db.scalar(
        select(MediaLibrary).where(
            MediaLibrary.tenant_id == tenant_id,
            MediaLibrary.id == command.library_id,
        )
    )
    if library is None:
        raise NotFound("media library not found")
    row = MediaAsset(
        tenant_id=tenant_id,
        library_id=library.id,
        kind=command.kind.value,
        title=_required(command.title, field="asset title"),
        description=_optional(command.description),
        default_alt_text=_optional(command.default_alt_text),
        creator_credit=_optional(command.creator_credit),
        photographer_credit=_optional(command.photographer_credit),
        producer_credit=_optional(command.producer_credit),
        contributor_credits=[
            _required(value, field="contributor credit")
            for value in command.contributor_credits
        ],
        capture_date=command.capture_date,
        supplied_location=_optional(command.supplied_location),
        sensitivity=_optional(command.sensitivity),
        lifecycle=AssetLifecycle.INGESTING.value,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=row.id,
        event_type="asset_created",
        identity=f"asset:{row.id}:created",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={"kind": row.kind, "library_id": str(row.library_id)},
    )
    return row


def update_descriptive_metadata(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    title: str,
    description: str | None,
    default_alt_text: str | None,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaAsset:
    row = _asset(db, tenant_id, asset_id, for_update=True)
    before = fingerprint_of(
        {
            "title": row.title,
            "description": row.description,
            "default_alt_text": row.default_alt_text,
        }
    )
    row.title = _required(title, field="asset title")
    row.description = _optional(description)
    row.default_alt_text = _optional(default_alt_text)
    after = fingerprint_of(
        {
            "title": row.title,
            "description": row.description,
            "default_alt_text": row.default_alt_text,
        }
    )
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=row.id,
        event_type="descriptive_metadata_changed",
        identity=f"asset:{row.id}:metadata:{after}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={"before": before, "after": after},
    )
    return row


def add_revision(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    command: RevisionCommand,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaRevision:
    asset = _asset(db, tenant_id, asset_id, for_update=True)
    checksum = _required(command.checksum, field="checksum").lower()
    if command.byte_length < 0:
        raise InvalidEvidence("byte_length must not be negative")
    perceptual_hash = _optional(command.perceptual_hash)
    perceptual_hash_algorithm = _optional(command.perceptual_hash_algorithm)
    if bool(perceptual_hash) != bool(perceptual_hash_algorithm):
        raise InvalidEvidence("perceptual hash and algorithm must be supplied together")
    previous = db.scalar(
        select(func.max(MediaRevision.revision_number)).where(
            MediaRevision.tenant_id == tenant_id,
            MediaRevision.asset_id == asset.id,
        )
    )
    number = int(previous or 0) + 1
    row = MediaRevision(
        tenant_id=tenant_id,
        asset_id=asset.id,
        revision_number=number,
        file_id=command.file_id,
        checksum=checksum,
        media_type=_required(command.media_type, field="media_type").lower(),
        byte_length=command.byte_length,
        source_kind=command.source_kind.value,
        source_ref=_optional(command.source_ref),
        author_ref=_required(command.author_ref, field="author_ref"),
        source_created_at=command.created_at,
        change_reason=_required(command.change_reason, field="change reason"),
        perceptual_hash=perceptual_hash.lower() if perceptual_hash else None,
        perceptual_hash_algorithm=perceptual_hash_algorithm,
    )
    db.add(row)
    db.flush()
    asset.current_revision_id = row.id
    if asset.lifecycle in {
        AssetLifecycle.AVAILABLE.value,
        AssetLifecycle.RESTRICTED.value,
        AssetLifecycle.EXPIRED.value,
    }:
        asset.lifecycle = AssetLifecycle.INGESTING.value
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="revision_created",
        identity=f"asset:{asset.id}:revision:{number}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "revision_id": str(row.id),
            "revision_number": number,
            "file_id": str(row.file_id),
            "checksum": row.checksum,
            "media_type": row.media_type,
            "byte_length": row.byte_length,
            "source_kind": row.source_kind,
        },
    )
    return row


def _hamming_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def find_duplicate_revisions(
    db: Session,
    *,
    tenant_id: UUID,
    checksum: str | None = None,
    perceptual_hash: str | None = None,
    perceptual_hash_algorithm: str | None = None,
    max_hamming_distance: int = 0,
) -> tuple[DuplicateCandidate, ...]:
    checksum = _optional(checksum)
    perceptual_hash = _optional(perceptual_hash)
    if checksum is None and perceptual_hash is None:
        raise InvalidEvidence("checksum or perceptual_hash is required")
    if max_hamming_distance < 0:
        raise InvalidEvidence("max_hamming_distance must not be negative")
    matches: dict[UUID, DuplicateCandidate] = {}
    if checksum is not None:
        rows = db.scalars(
            select(MediaRevision).where(
                MediaRevision.tenant_id == tenant_id,
                MediaRevision.checksum == checksum.lower(),
            )
        ).all()
        for row in rows:
            matches[row.id] = DuplicateCandidate(
                revision_id=row.id,
                asset_id=row.asset_id,
                match_kind="exact",
                hamming_distance=None,
            )
    if perceptual_hash is not None:
        algorithm = _required(
            perceptual_hash_algorithm or "", field="perceptual hash algorithm"
        )
        rows = db.scalars(
            select(MediaRevision).where(
                MediaRevision.tenant_id == tenant_id,
                MediaRevision.perceptual_hash_algorithm == algorithm,
                MediaRevision.perceptual_hash.is_not(None),
            )
        ).all()
        for row in rows:
            stored_hash = row.perceptual_hash
            if stored_hash is None:
                continue
            distance = _hamming_distance(perceptual_hash.lower(), stored_hash)
            if distance is None or distance > max_hamming_distance:
                continue
            existing = matches.get(row.id)
            if existing is None:
                matches[row.id] = DuplicateCandidate(
                    revision_id=row.id,
                    asset_id=row.asset_id,
                    match_kind="perceptual",
                    hamming_distance=distance,
                )
    return tuple(sorted(matches.values(), key=lambda item: str(item.revision_id)))


def record_metadata_observation(
    db: Session,
    *,
    tenant_id: UUID,
    command: MetadataObservationCommand,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaMetadataObservation:
    revision = _revision(db, tenant_id, command.revision_id)
    if command.source_checksum.lower() != revision.checksum:
        raise InvalidEvidence("metadata source checksum does not match revision")
    for value, field in (
        (command.width, "width"),
        (command.height, "height"),
        (command.bitrate, "bitrate"),
        (command.duration_seconds, "duration_seconds"),
        (command.frame_rate, "frame_rate"),
    ):
        if value is not None and value < 0:
            raise InvalidEvidence(f"{field} must not be negative")
    row = MediaMetadataObservation(
        tenant_id=tenant_id,
        revision_id=revision.id,
        source_checksum=revision.checksum,
        extractor_code=_required(command.extractor_code, field="extractor code"),
        extractor_version=_required(
            command.extractor_version, field="extractor version"
        ),
        observed_at=command.observed_at,
        width=command.width,
        height=command.height,
        duration_seconds=command.duration_seconds,
        frame_rate=command.frame_rate,
        bitrate=command.bitrate,
        codec=_optional(command.codec),
        colour_profile=_optional(command.colour_profile),
        orientation=_optional(command.orientation),
        accessibility=dict(command.accessibility),
        exif=dict(command.exif),
        iptc=dict(command.iptc),
        xmp=dict(command.xmp),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="metadata_observed",
        identity=f"revision:{revision.id}:metadata:{row.id}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "observation_id": str(row.id),
            "extractor_code": row.extractor_code,
            "extractor_version": row.extractor_version,
            "source_checksum": row.source_checksum,
        },
    )
    return row


def create_collection(
    db: Session,
    *,
    tenant_id: UUID,
    library_id: UUID,
    code: str,
    name: str,
    description: str | None,
    selection_kind: str,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaCollection:
    library = db.scalar(
        select(MediaLibrary).where(
            MediaLibrary.tenant_id == tenant_id, MediaLibrary.id == library_id
        )
    )
    if library is None:
        raise NotFound("media library not found")
    row = MediaCollection(
        tenant_id=tenant_id,
        library_id=library.id,
        code=_required(code, field="collection code").lower(),
        name=_required(name, field="collection name"),
        description=_optional(description),
        selection_kind=_required(selection_kind, field="selection kind"),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="collection",
        aggregate_id=row.id,
        event_type="collection_created",
        identity=f"collection:{row.id}:created",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={"library_id": str(library.id), "code": row.code},
    )
    return row


def add_collection_item(
    db: Session,
    *,
    tenant_id: UUID,
    collection_id: UUID,
    asset_id: UUID,
    sort_order: int,
    is_featured: bool,
    is_default: bool,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaCollectionItem:
    if sort_order < 0:
        raise InvalidEvidence("sort_order must not be negative")
    collection = db.scalar(
        select(MediaCollection).where(
            MediaCollection.tenant_id == tenant_id,
            MediaCollection.id == collection_id,
        )
    )
    asset = _asset(db, tenant_id, asset_id)
    if collection is None:
        raise NotFound("media collection not found")
    if collection.library_id != asset.library_id:
        raise InvalidEvidence("collection and asset must belong to the same library")
    existing = db.scalar(
        select(MediaCollectionItem).where(
            MediaCollectionItem.tenant_id == tenant_id,
            MediaCollectionItem.collection_id == collection.id,
            MediaCollectionItem.asset_id == asset.id,
        )
    )
    if existing is not None:
        return existing
    row = MediaCollectionItem(
        tenant_id=tenant_id,
        collection_id=collection.id,
        asset_id=asset.id,
        sort_order=sort_order,
        is_featured=is_featured,
        is_default=is_default,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="collection",
        aggregate_id=collection.id,
        event_type="collection_item_added",
        identity=f"collection:{collection.id}:asset:{asset.id}:added",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "asset_id": str(asset.id),
            "sort_order": sort_order,
            "is_featured": is_featured,
            "is_default": is_default,
        },
    )
    return row


def assign_classification(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    assignment_kind: str,
    vocabulary_ref: str,
    code: str,
    hierarchy_path: str | None,
    source_owner: str | None,
    source_type: str | None,
    source_ref: str | None,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaClassificationAssignment:
    asset = _asset(db, tenant_id, asset_id)
    if assignment_kind not in {"classification", "tag", "association"}:
        raise InvalidEvidence("unsupported classification assignment kind")
    row = MediaClassificationAssignment(
        tenant_id=tenant_id,
        asset_id=asset.id,
        assignment_kind=assignment_kind,
        vocabulary_ref=_required(vocabulary_ref, field="vocabulary_ref"),
        code=_required(code, field="classification code"),
        hierarchy_path=_optional(hierarchy_path),
        source_owner=_optional(source_owner),
        source_type=_optional(source_type),
        source_ref=_optional(source_ref) or "",
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="classification_assigned",
        identity=f"asset:{asset.id}:classification:{row.id}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "assignment_id": str(row.id),
            "kind": row.assignment_kind,
            "vocabulary_ref": row.vocabulary_ref,
            "code": row.code,
        },
    )
    return row


def relate_revisions(
    db: Session,
    *,
    tenant_id: UUID,
    from_revision_id: UUID,
    to_revision_id: UUID,
    relation: str,
    language_code: str | None,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaRelationship:
    if from_revision_id == to_revision_id:
        raise InvalidEvidence("a revision cannot relate to itself")
    source = _revision(db, tenant_id, from_revision_id)
    target = _revision(db, tenant_id, to_revision_id)
    if relation not in {
        "derived_from",
        "parent_of",
        "alternate_language_of",
        "related",
    }:
        raise InvalidEvidence("unsupported media relationship")
    if relation == "alternate_language_of" and _optional(language_code) is None:
        raise InvalidEvidence("alternate language relationship requires language_code")
    existing = db.scalar(
        select(MediaRelationship).where(
            MediaRelationship.tenant_id == tenant_id,
            MediaRelationship.from_revision_id == source.id,
            MediaRelationship.to_revision_id == target.id,
            MediaRelationship.relation == relation,
        )
    )
    if existing is not None:
        return existing
    row = MediaRelationship(
        tenant_id=tenant_id,
        from_revision_id=source.id,
        to_revision_id=target.id,
        relation=relation,
        language_code=_optional(language_code),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=source.id,
        event_type="revision_related",
        identity=f"revision:{source.id}:relation:{row.id}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "to_revision_id": str(target.id),
            "relation": relation,
            "language_code": row.language_code,
        },
    )
    return row


def set_rights(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    command: RightsCommand,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaRightsVersion:
    asset = _asset(db, tenant_id, asset_id, for_update=True)
    if command.starts_at and command.ends_at and command.starts_at >= command.ends_at:
        raise RightsError("rights start must be before end")
    if command.review_at and command.ends_at and command.review_at > command.ends_at:
        raise RightsError("rights review cannot be after expiry")
    if command.release_references and not command.release_evidence_ref:
        raise RightsError("release references require decision evidence")
    if command.release_evidence_valid and not command.release_evidence_ref:
        raise RightsError("valid release evidence requires an evidence reference")
    previous = db.scalar(
        select(func.max(MediaRightsVersion.version_number)).where(
            MediaRightsVersion.tenant_id == tenant_id,
            MediaRightsVersion.asset_id == asset.id,
        )
    )
    number = int(previous or 0) + 1
    row = MediaRightsVersion(
        tenant_id=tenant_id,
        asset_id=asset.id,
        version_number=number,
        rights_holder=_required(command.rights_holder, field="rights holder"),
        copyright_notice=_optional(command.copyright_notice),
        licence_id=_required(command.licence_id, field="licence id"),
        licence_version=_required(command.licence_version, field="licence version"),
        territories=sorted(
            {_required(item, field="territory") for item in command.territories}
        ),
        channels=sorted(
            {_required(item, field="channel") for item in command.channels}
        ),
        purposes=sorted(
            {_required(item, field="purpose") for item in command.purposes}
        ),
        starts_at=command.starts_at,
        ends_at=command.ends_at,
        required_credit=_optional(command.required_credit),
        commercial_use_allowed=command.commercial_use_allowed,
        modification_allowed=command.modification_allowed,
        release_references=[
            _required(item, field="release reference")
            for item in command.release_references
        ],
        release_evidence_ref=_optional(command.release_evidence_ref),
        release_evidence_valid=command.release_evidence_valid,
        sensitivity=_optional(command.sensitivity),
        embargo_until=command.embargo_until,
        review_at=command.review_at,
    )
    db.add(row)
    db.flush()
    asset.current_rights_version_id = row.id
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="rights_version_created",
        identity=f"asset:{asset.id}:rights:{number}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "rights_version_id": str(row.id),
            "version_number": number,
            "licence_id": row.licence_id,
            "licence_version": row.licence_version,
            "ends_at": row.ends_at.isoformat() if row.ends_at else None,
            "review_at": row.review_at.isoformat() if row.review_at else None,
        },
    )
    for purpose, due_at in (("review", row.review_at), ("expiry", row.ends_at)):
        if due_at is None:
            continue
        _event(
            db,
            tenant_id=tenant_id,
            aggregate_type="asset",
            aggregate_id=asset.id,
            event_type="rights_timer_requested",
            identity=f"asset:{asset.id}:rights:{row.id}:timer:{purpose}",
            actor_ref=actor_ref,
            occurred_at=recorded_at,
            payload={
                "rights_version_id": str(row.id),
                "purpose": purpose,
                "due_at": due_at.isoformat(),
            },
        )
    return row


def evaluate_usage(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    use: RightsUse,
) -> RightsDecision:
    asset = _asset(db, tenant_id, asset_id)
    lifecycle_reasons = (
        ()
        if asset.lifecycle == AssetLifecycle.AVAILABLE.value
        else (f"lifecycle_{asset.lifecycle}",)
    )
    if asset.current_rights_version_id is None:
        return RightsDecision(
            False,
            tuple(sorted((*lifecycle_reasons, "missing_rights"))),
            None,
            None,
            None,
        )
    rights = db.scalar(
        select(MediaRightsVersion).where(
            MediaRightsVersion.tenant_id == tenant_id,
            MediaRightsVersion.id == asset.current_rights_version_id,
        )
    )
    if rights is None:
        raise InvalidEvidence("current rights pointer is broken")
    reasons = list(lifecycle_reasons)
    if rights.starts_at is not None and use.at < rights.starts_at:
        reasons.append("rights_not_started")
    if rights.ends_at is not None and use.at >= rights.ends_at:
        reasons.append("rights_expired")
    if rights.embargo_until is not None and use.at < rights.embargo_until:
        reasons.append("embargoed")
    if (
        rights.territories
        and "*" not in rights.territories
        and use.territory not in rights.territories
    ):
        reasons.append("territory_forbidden")
    if (
        rights.channels
        and "*" not in rights.channels
        and use.channel not in rights.channels
    ):
        reasons.append("channel_forbidden")
    if (
        rights.purposes
        and "*" not in rights.purposes
        and use.purpose not in rights.purposes
    ):
        reasons.append("purpose_forbidden")
    if use.commercial and not rights.commercial_use_allowed:
        reasons.append("commercial_use_forbidden")
    if use.modifies and not rights.modification_allowed:
        reasons.append("modification_forbidden")
    if rights.release_references and not rights.release_evidence_valid:
        reasons.append("release_evidence_invalid")
    return RightsDecision(
        allowed=not reasons,
        reasons=tuple(sorted(reasons)),
        rights_version_id=rights.id,
        required_credit=rights.required_credit,
        evidence_ref=rights.release_evidence_ref,
    )


def apply_rights_deadline(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    observation: RightsDeadlineObservation,
    actor_ref: str,
    observed_at: datetime,
) -> MediaAsset:
    """Apply an exact Durable Timers callback without scanning an ambient clock."""
    asset = _asset(db, tenant_id, asset_id, for_update=True)
    rights = db.scalar(
        select(MediaRightsVersion).where(
            MediaRightsVersion.tenant_id == tenant_id,
            MediaRightsVersion.id == observation.rights_version_id,
            MediaRightsVersion.asset_id == asset.id,
        )
    )
    if rights is None:
        raise NotFound("media rights version not found")
    due_at = (
        rights.review_at
        if observation.purpose == RightsDeadlinePurpose.REVIEW
        else rights.ends_at
    )
    if due_at is None or due_at != observation.due_at:
        raise InvalidEvidence(
            "rights deadline does not match immutable rights evidence"
        )
    if observed_at < due_at:
        raise InvalidEvidence("rights deadline arrived before its due time")
    source_event_id = _required(
        observation.source_event_id, field="deadline source_event_id"
    )
    identity = f"rights-deadline:{source_event_id}"
    existing = db.scalar(
        select(MediaEvent).where(
            MediaEvent.tenant_id == tenant_id,
            MediaEvent.event_identity == identity,
        )
    )
    if existing is not None:
        if (
            existing.aggregate_id != asset.id
            or existing.payload.get("rights_version_id") != str(rights.id)
            or existing.payload.get("purpose") != observation.purpose.value
            or existing.payload.get("due_at") != due_at.isoformat()
        ):
            raise Conflict(
                "rights deadline source event was reused for different evidence"
            )
        return asset

    action = "ignored_superseded"
    before = asset.lifecycle
    if asset.current_rights_version_id == rights.id:
        current = AssetLifecycle(asset.lifecycle)
        terminal = {AssetLifecycle.WITHDRAWN, AssetLifecycle.ARCHIVED}
        if current not in terminal:
            target = (
                AssetLifecycle.RESTRICTED
                if observation.purpose == RightsDeadlinePurpose.REVIEW
                else AssetLifecycle.EXPIRED
            )
            if current != target and target in _TRANSITIONS[current]:
                asset.lifecycle = target.value
                db.flush()
                action = f"transitioned_{target.value}"
            elif current == target:
                action = f"already_{target.value}"
            else:
                action = f"ignored_{current.value}"
        else:
            action = "ignored_terminal"
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="rights_deadline_observed",
        identity=identity,
        actor_ref=actor_ref,
        occurred_at=observed_at,
        payload={
            "rights_version_id": str(rights.id),
            "purpose": observation.purpose.value,
            "due_at": due_at.isoformat(),
            "before": before,
            "after": asset.lifecycle,
            "action": action,
        },
    )
    return asset


def request_rendition(
    db: Session,
    *,
    tenant_id: UUID,
    command: RenditionCommand,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaRendition:
    revision = _revision(db, tenant_id, command.source_revision_id)
    if command.source_checksum.lower() != revision.checksum:
        raise InvalidEvidence("rendition source checksum does not match revision")
    if command.requested_width is not None and command.requested_width <= 0:
        raise InvalidEvidence("requested width must be positive")
    if command.requested_height is not None and command.requested_height <= 0:
        raise InvalidEvidence("requested height must be positive")
    recipe_code = _required(command.recipe_code, field="recipe code")
    recipe_version = _required(command.recipe_version, field="recipe version")
    engine_code = _required(command.engine_code, field="engine code")
    engine_version = _required(command.engine_version, field="engine version")
    output_media_type = _required(
        command.output_media_type, field="output media type"
    ).lower()
    request_fingerprint = fingerprint_of(
        {
            "source_revision_id": str(revision.id),
            "source_checksum": revision.checksum,
            "kind": command.kind.value,
            "recipe_code": recipe_code,
            "recipe_version": recipe_version,
            "engine_code": engine_code,
            "engine_version": engine_version,
            "output_media_type": output_media_type,
            "requested_width": command.requested_width,
            "requested_height": command.requested_height,
            "focal_point": command.focal_point,
            "parameters": command.parameters,
        }
    )
    existing = db.scalar(
        select(MediaRendition).where(
            MediaRendition.tenant_id == tenant_id,
            MediaRendition.source_revision_id == revision.id,
            MediaRendition.request_fingerprint == request_fingerprint,
        )
    )
    if existing is not None:
        return existing
    row = MediaRendition(
        tenant_id=tenant_id,
        source_revision_id=revision.id,
        source_checksum=revision.checksum,
        kind=command.kind.value,
        recipe_code=recipe_code,
        recipe_version=recipe_version,
        engine_code=engine_code,
        engine_version=engine_version,
        parameters=dict(command.parameters),
        focal_point=dict(command.focal_point),
        requested_width=command.requested_width,
        requested_height=command.requested_height,
        output_media_type=output_media_type,
        request_fingerprint=request_fingerprint,
        attempt_number=1,
        state=RenditionState.REQUESTED.value,
        output_byte_length=0,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="rendition_requested",
        identity=f"revision:{revision.id}:rendition:{request_fingerprint}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "rendition_id": str(row.id),
            "kind": row.kind,
            "recipe_code": row.recipe_code,
            "recipe_version": row.recipe_version,
            "engine_code": row.engine_code,
            "engine_version": row.engine_version,
            "source_checksum": row.source_checksum,
        },
    )
    return row


def request_rendition_regeneration(
    db: Session,
    *,
    tenant_id: UUID,
    rendition_id: UUID,
    reason: str,
    actor_ref: str,
    requested_at: datetime,
) -> MediaRendition:
    """Reset a repairable rendition while preserving its stable recipe identity."""
    row = db.scalar(
        select(MediaRendition)
        .where(
            MediaRendition.tenant_id == tenant_id,
            MediaRendition.id == rendition_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound("media rendition not found")
    revision = _revision(db, tenant_id, row.source_revision_id)
    if revision.checksum != row.source_checksum:
        raise InvalidEvidence("rendition source evidence is stale")
    if row.state == RenditionState.REQUESTED.value:
        return row
    regeneration_reason = _required(reason, field="regeneration reason")
    row.attempt_number += 1
    row.state = RenditionState.REQUESTED.value
    row.output_file_id = None
    row.output_checksum = None
    row.output_byte_length = 0
    row.output_width = None
    row.output_height = None
    row.output_duration_seconds = None
    row.output_codec = None
    row.completed_at = None
    row.failure_code = None
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="rendition_regeneration_requested",
        identity=f"rendition:{row.id}:attempt:{row.attempt_number}:requested",
        actor_ref=actor_ref,
        occurred_at=requested_at,
        payload={
            "rendition_id": str(row.id),
            "attempt_number": row.attempt_number,
            "reason": regeneration_reason,
            "source_checksum": row.source_checksum,
        },
    )
    return row


def complete_rendition(
    db: Session,
    *,
    tenant_id: UUID,
    rendition_id: UUID,
    output: RenditionOutput,
    actor_ref: str,
    completed_at: datetime,
) -> MediaRendition:
    row = db.scalar(
        select(MediaRendition)
        .where(
            MediaRendition.tenant_id == tenant_id,
            MediaRendition.id == rendition_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound("media rendition not found")
    revision = _revision(db, tenant_id, row.source_revision_id)
    if revision.checksum != row.source_checksum:
        raise InvalidEvidence("rendition source evidence is stale")
    if output.byte_length < 0:
        raise InvalidEvidence("rendition byte length must not be negative")
    if row.state == RenditionState.READY.value:
        if (
            row.output_file_id == output.file_id
            and row.output_checksum == output.checksum.lower()
        ):
            return row
        raise Conflict("ready rendition cannot be replaced with different output")
    if row.state == RenditionState.FAILED.value:
        raise Conflict("failed rendition must be requested for regeneration")
    row.output_file_id = output.file_id
    row.output_checksum = _required(output.checksum, field="output checksum").lower()
    row.output_byte_length = output.byte_length
    row.output_width = output.width
    row.output_height = output.height
    row.output_duration_seconds = output.duration_seconds
    row.output_codec = _optional(output.codec)
    row.state = RenditionState.READY.value
    row.completed_at = completed_at
    row.failure_code = None
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="rendition_completed",
        identity=(
            f"rendition:{row.id}:attempt:{row.attempt_number}:"
            f"output:{row.output_checksum}"
        ),
        actor_ref=actor_ref,
        occurred_at=completed_at,
        payload={
            "rendition_id": str(row.id),
            "attempt_number": row.attempt_number,
            "output_file_id": str(row.output_file_id),
            "output_checksum": row.output_checksum,
            "output_byte_length": row.output_byte_length,
        },
    )
    return row


def fail_rendition(
    db: Session,
    *,
    tenant_id: UUID,
    rendition_id: UUID,
    failure_code: str,
    actor_ref: str,
    failed_at: datetime,
) -> MediaRendition:
    row = db.scalar(
        select(MediaRendition)
        .where(
            MediaRendition.tenant_id == tenant_id,
            MediaRendition.id == rendition_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound("media rendition not found")
    if row.state == RenditionState.READY.value:
        raise Conflict("a completed rendition cannot be failed")
    normalized_failure = _required(failure_code, field="failure code")
    if row.state == RenditionState.FAILED.value:
        if row.failure_code == normalized_failure:
            return row
        raise Conflict("rendition attempt already failed with a different code")
    row.state = RenditionState.FAILED.value
    row.failure_code = normalized_failure
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=row.source_revision_id,
        event_type="rendition_failed",
        identity=(
            f"rendition:{row.id}:attempt:{row.attempt_number}:"
            f"failed:{row.failure_code}"
        ),
        actor_ref=actor_ref,
        occurred_at=failed_at,
        payload={
            "rendition_id": str(row.id),
            "attempt_number": row.attempt_number,
            "failure_code": row.failure_code,
        },
    )
    return row


def rendition_is_stale(
    rendition: MediaRendition,
    source_revision: MediaRevision,
    *,
    recipe_version: str,
    engine_version: str,
) -> bool:
    return (
        rendition.source_revision_id != source_revision.id
        or rendition.source_checksum != source_revision.checksum
        or rendition.recipe_version != recipe_version
        or rendition.engine_version != engine_version
        or rendition.state != RenditionState.READY.value
        or rendition.output_file_id is None
        or rendition.output_checksum is None
    )


def stale_renditions(
    db: Session,
    *,
    tenant_id: UUID,
    source_revision_id: UUID,
    recipe_version: str,
    engine_version: str,
) -> tuple[MediaRendition, ...]:
    revision = _revision(db, tenant_id, source_revision_id)
    rows = db.scalars(
        select(MediaRendition).where(
            MediaRendition.tenant_id == tenant_id,
            MediaRendition.source_revision_id == revision.id,
        )
    ).all()
    return tuple(
        row
        for row in rows
        if rendition_is_stale(
            row,
            revision,
            recipe_version=recipe_version,
            engine_version=engine_version,
        )
    )


def observe_scan(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    observation: ScanObservation,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaEvent:
    asset = _asset(db, tenant_id, asset_id)
    revision = _revision(db, tenant_id, observation.revision_id)
    if revision.asset_id != asset.id:
        raise InvalidEvidence("scan revision does not belong to asset")
    if revision.file_id != observation.file_id:
        raise InvalidEvidence("scan file id does not match revision")
    if revision.checksum != observation.checksum.lower():
        raise InvalidEvidence("scan checksum does not match revision")
    if observation.decision not in {"safe", "quarantined", "failed"}:
        raise InvalidEvidence("unsupported scan decision")
    return _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="scan_observed",
        identity=f"asset:{asset.id}:scan:{observation.scanner_ref}",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "revision_id": str(revision.id),
            "file_id": str(revision.file_id),
            "checksum": revision.checksum,
            "decision": observation.decision,
            "scanner_ref": _required(observation.scanner_ref, field="scanner_ref"),
            "observed_at": observation.observed_at.isoformat(),
        },
    )


def observe_approval(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    revision_id: UUID,
    checksum: str,
    approval_ref: str,
    decision: str,
    actor_ref: str,
    observed_at: datetime,
) -> MediaEvent:
    asset = _asset(db, tenant_id, asset_id)
    revision = _revision(db, tenant_id, revision_id)
    if revision.asset_id != asset.id or revision.checksum != checksum.lower():
        raise InvalidEvidence("approval does not bind the exact asset revision")
    if decision not in {"approved", "rejected"}:
        raise InvalidEvidence("unsupported approval decision")
    reference = _required(approval_ref, field="approval_ref")
    return _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="approval_observed",
        identity=f"asset:{asset.id}:approval:{reference}",
        actor_ref=actor_ref,
        occurred_at=observed_at,
        payload={
            "revision_id": str(revision.id),
            "checksum": revision.checksum,
            "approval_ref": reference,
            "decision": decision,
        },
    )


_TRANSITIONS: dict[AssetLifecycle, frozenset[AssetLifecycle]] = {
    AssetLifecycle.INGESTING: frozenset(
        {AssetLifecycle.QUARANTINED, AssetLifecycle.AVAILABLE, AssetLifecycle.WITHDRAWN}
    ),
    AssetLifecycle.QUARANTINED: frozenset(
        {AssetLifecycle.INGESTING, AssetLifecycle.AVAILABLE, AssetLifecycle.WITHDRAWN}
    ),
    AssetLifecycle.AVAILABLE: frozenset(
        {
            AssetLifecycle.RESTRICTED,
            AssetLifecycle.EXPIRED,
            AssetLifecycle.WITHDRAWN,
            AssetLifecycle.ARCHIVED,
        }
    ),
    AssetLifecycle.RESTRICTED: frozenset(
        {
            AssetLifecycle.AVAILABLE,
            AssetLifecycle.EXPIRED,
            AssetLifecycle.WITHDRAWN,
            AssetLifecycle.ARCHIVED,
        }
    ),
    AssetLifecycle.EXPIRED: frozenset(
        {AssetLifecycle.AVAILABLE, AssetLifecycle.RESTRICTED, AssetLifecycle.ARCHIVED}
    ),
    AssetLifecycle.WITHDRAWN: frozenset({AssetLifecycle.ARCHIVED}),
    AssetLifecycle.ARCHIVED: frozenset(),
}


def _has_safe_scan(
    db: Session, *, tenant_id: UUID, asset_id: UUID, revision: MediaRevision
) -> bool:
    events = db.scalars(
        select(MediaEvent).where(
            MediaEvent.tenant_id == tenant_id,
            MediaEvent.aggregate_type == "asset",
            MediaEvent.aggregate_id == asset_id,
            MediaEvent.event_type == "scan_observed",
        )
    ).all()
    return any(
        event.payload.get("revision_id") == str(revision.id)
        and event.payload.get("checksum") == revision.checksum
        and event.payload.get("decision") == "safe"
        for event in events
    )


def transition_asset(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    target: AssetLifecycle,
    reason: str,
    actor_ref: str,
    effective_at: datetime,
) -> MediaAsset:
    asset = _asset(db, tenant_id, asset_id, for_update=True)
    current = AssetLifecycle(asset.lifecycle)
    if target == current:
        return asset
    if target not in _TRANSITIONS[current]:
        raise LifecycleError(
            f"cannot transition media asset from {current} to {target}"
        )
    if target == AssetLifecycle.AVAILABLE:
        if asset.current_revision_id is None:
            raise LifecycleError("available asset requires a current revision")
        revision = _revision(db, tenant_id, asset.current_revision_id)
        if not _has_safe_scan(
            db, tenant_id=tenant_id, asset_id=asset.id, revision=revision
        ):
            raise LifecycleError(
                "available asset requires an exact safe scan observation"
            )
    asset.lifecycle = target.value
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="asset",
        aggregate_id=asset.id,
        event_type="lifecycle_transitioned",
        identity=f"asset:{asset.id}:lifecycle:{current.value}:{target.value}:{effective_at.isoformat()}",
        actor_ref=actor_ref,
        occurred_at=effective_at,
        payload={
            "from": current.value,
            "to": target.value,
            "reason": _required(reason, field="transition reason"),
        },
    )
    return asset


def grant_access(
    db: Session,
    *,
    tenant_id: UUID,
    command: AccessGrantCommand,
    actor_ref: str,
    recorded_at: datetime,
) -> MediaAccessGrant:
    library_id: UUID | None = None
    collection_id: UUID | None = None
    asset_id: UUID | None = None
    if command.scope == AccessScope.LIBRARY:
        library_id = command.scope_id
        exists = db.scalar(
            select(MediaLibrary.id).where(
                MediaLibrary.tenant_id == tenant_id,
                MediaLibrary.id == library_id,
            )
        )
    elif command.scope == AccessScope.COLLECTION:
        collection_id = command.scope_id
        exists = db.scalar(
            select(MediaCollection.id).where(
                MediaCollection.tenant_id == tenant_id,
                MediaCollection.id == collection_id,
            )
        )
    else:
        asset_id = command.scope_id
        exists = db.scalar(
            select(MediaAsset.id).where(
                MediaAsset.tenant_id == tenant_id, MediaAsset.id == asset_id
            )
        )
    if exists is None:
        raise NotFound("access grant scope not found")
    principal_ref = _required(command.principal_ref, field="principal_ref")
    existing = db.scalar(
        select(MediaAccessGrant).where(
            MediaAccessGrant.tenant_id == tenant_id,
            MediaAccessGrant.library_id.is_(library_id)
            if library_id is None
            else MediaAccessGrant.library_id == library_id,
            MediaAccessGrant.collection_id.is_(collection_id)
            if collection_id is None
            else MediaAccessGrant.collection_id == collection_id,
            MediaAccessGrant.asset_id.is_(asset_id)
            if asset_id is None
            else MediaAccessGrant.asset_id == asset_id,
            MediaAccessGrant.principal_ref == principal_ref,
            MediaAccessGrant.permission == command.permission.value,
        )
    )
    if existing is not None:
        if (
            existing.effect == command.effect.value
            and existing.expires_at == command.expires_at
            and existing.revoked_at is None
        ):
            return existing
        raise Conflict("access grant already exists with different terms")
    row = MediaAccessGrant(
        tenant_id=tenant_id,
        library_id=library_id,
        collection_id=collection_id,
        asset_id=asset_id,
        principal_type=_required(command.principal_type, field="principal_type"),
        principal_ref=principal_ref,
        permission=command.permission.value,
        effect=command.effect.value,
        expires_at=command.expires_at,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type=command.scope.value,
        aggregate_id=command.scope_id,
        event_type="access_granted",
        identity=f"access-grant:{row.id}:created",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={
            "grant_id": str(row.id),
            "principal_type": row.principal_type,
            "principal_ref": row.principal_ref,
            "permission": row.permission,
            "effect": row.effect,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )
    return row


def revoke_access(
    db: Session,
    *,
    tenant_id: UUID,
    grant_id: UUID,
    actor_ref: str,
    revoked_at: datetime,
) -> MediaAccessGrant:
    row = db.scalar(
        select(MediaAccessGrant)
        .where(
            MediaAccessGrant.tenant_id == tenant_id,
            MediaAccessGrant.id == grant_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound("access grant not found")
    if row.revoked_at is not None:
        return row
    row.revoked_at = revoked_at
    db.flush()
    aggregate_id = row.asset_id or row.collection_id or row.library_id
    if aggregate_id is None:
        raise InvalidEvidence("access grant has no scope")
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="access_scope",
        aggregate_id=aggregate_id,
        event_type="access_revoked",
        identity=f"access-grant:{row.id}:revoked",
        actor_ref=actor_ref,
        occurred_at=revoked_at,
        payload={"grant_id": str(row.id)},
    )
    return row


def may_access(
    db: Session,
    *,
    tenant_id: UUID,
    asset_id: UUID,
    principal_refs: Sequence[str],
    permission: Permission,
    at: datetime,
) -> AccessDecision:
    if not principal_refs:
        return AccessDecision(False, "no_principal", None)
    asset = _asset(db, tenant_id, asset_id)
    collection_ids = tuple(
        db.scalars(
            select(MediaCollectionItem.collection_id).where(
                MediaCollectionItem.tenant_id == tenant_id,
                MediaCollectionItem.asset_id == asset.id,
            )
        ).all()
    )
    scopes = or_(
        MediaAccessGrant.asset_id == asset.id,
        MediaAccessGrant.collection_id.in_(tuple(collection_ids))
        if collection_ids
        else false(),
        MediaAccessGrant.library_id == asset.library_id,
    )
    rows = db.scalars(
        select(MediaAccessGrant).where(
            MediaAccessGrant.tenant_id == tenant_id,
            MediaAccessGrant.principal_ref.in_(tuple(principal_refs)),
            MediaAccessGrant.permission == permission.value,
            MediaAccessGrant.revoked_at.is_(None),
            or_(
                MediaAccessGrant.expires_at.is_(None), MediaAccessGrant.expires_at > at
            ),
            scopes,
        )
    ).all()
    if not rows:
        return AccessDecision(False, "no_grant", None)

    def rank(grant: MediaAccessGrant) -> int:
        if grant.asset_id is not None:
            return 3
        if grant.collection_id is not None:
            return 2
        return 1

    nearest = max(rank(row) for row in rows)
    candidates = [row for row in rows if rank(row) == nearest]
    denied = next(
        (row for row in candidates if row.effect == GrantEffect.DENY.value), None
    )
    if denied is not None:
        return AccessDecision(False, "explicit_deny", denied.id)
    allowed = candidates[0]
    return AccessDecision(True, "explicit_allow", allowed.id)


def add_annotation(
    db: Session,
    *,
    tenant_id: UUID,
    revision_id: UUID,
    author_ref: str,
    body: str,
    anchor: Mapping[str, object],
    recorded_at: datetime,
) -> MediaAnnotation:
    revision = _revision(db, tenant_id, revision_id)
    row = MediaAnnotation(
        tenant_id=tenant_id,
        revision_id=revision.id,
        author_ref=_required(author_ref, field="author_ref"),
        body=_required(body, field="annotation body"),
        anchor=dict(anchor),
        status="open",
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="annotation_added",
        identity=f"revision:{revision.id}:annotation:{row.id}",
        actor_ref=author_ref,
        occurred_at=recorded_at,
        payload={"annotation_id": str(row.id)},
    )
    return row


def resolve_annotation(
    db: Session,
    *,
    tenant_id: UUID,
    annotation_id: UUID,
    status: str,
    actor_ref: str,
    resolved_at: datetime,
) -> MediaAnnotation:
    if status not in {"resolved", "dismissed"}:
        raise InvalidEvidence("annotation resolution must be resolved or dismissed")
    row = db.scalar(
        select(MediaAnnotation)
        .where(
            MediaAnnotation.tenant_id == tenant_id,
            MediaAnnotation.id == annotation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound("media annotation not found")
    if row.status != "open":
        if row.status == status:
            return row
        raise Conflict("annotation already has a different terminal state")
    row.status = status
    row.resolved_at = resolved_at
    row.resolved_by_ref = _required(actor_ref, field="actor_ref")
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=row.revision_id,
        event_type="annotation_resolved",
        identity=f"annotation:{row.id}:{status}",
        actor_ref=actor_ref,
        occurred_at=resolved_at,
        payload={"annotation_id": str(row.id), "status": status},
    )
    return row


def save_selection(
    db: Session,
    *,
    tenant_id: UUID,
    library_id: UUID,
    owner_ref: str,
    name: str,
    criteria: Mapping[str, object],
    actor_ref: str,
    recorded_at: datetime,
) -> MediaSavedSelection:
    library = db.scalar(
        select(MediaLibrary).where(
            MediaLibrary.tenant_id == tenant_id, MediaLibrary.id == library_id
        )
    )
    if library is None:
        raise NotFound("media library not found")
    row = MediaSavedSelection(
        tenant_id=tenant_id,
        library_id=library.id,
        owner_ref=_required(owner_ref, field="owner_ref"),
        name=_required(name, field="selection name"),
        criteria=dict(criteria),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="library",
        aggregate_id=library.id,
        event_type="selection_saved",
        identity=f"selection:{row.id}:created",
        actor_ref=actor_ref,
        occurred_at=recorded_at,
        payload={"selection_id": str(row.id), "owner_ref": row.owner_ref},
    )
    return row


def observe_usage(
    db: Session,
    *,
    tenant_id: UUID,
    command: UsageObservationCommand,
) -> MediaUsageObservation:
    revision = _revision(db, tenant_id, command.revision_id)
    if command.rendition_id is not None:
        rendition = db.scalar(
            select(MediaRendition).where(
                MediaRendition.tenant_id == tenant_id,
                MediaRendition.id == command.rendition_id,
            )
        )
        if rendition is None or rendition.source_revision_id != revision.id:
            raise InvalidEvidence("usage rendition does not derive from revision")
    source_owner = _required(command.source_owner, field="source_owner")
    event_id = _required(command.source_event_id, field="source_event_id")
    existing = db.scalar(
        select(MediaUsageObservation).where(
            MediaUsageObservation.tenant_id == tenant_id,
            MediaUsageObservation.source_owner == source_owner,
            MediaUsageObservation.source_event_id == event_id,
        )
    )
    if existing is not None:
        if existing.source_fingerprint == command.source_fingerprint:
            return existing
        raise Conflict("usage source event was replayed with a different fingerprint")
    row = MediaUsageObservation(
        tenant_id=tenant_id,
        source_owner=source_owner,
        source_type=_required(command.source_type, field="source_type"),
        source_id=_required(command.source_id, field="source_id"),
        source_version=_required(command.source_version, field="source_version"),
        relation=_required(command.relation, field="relation"),
        revision_id=revision.id,
        rendition_id=command.rendition_id,
        source_event_id=event_id,
        source_fingerprint=_required(
            command.source_fingerprint, field="source_fingerprint"
        ),
        active=command.active,
        observed_at=command.observed_at,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_type="revision",
        aggregate_id=revision.id,
        event_type="usage_observed",
        identity=f"usage:{source_owner}:{event_id}",
        actor_ref=f"source:{source_owner}",
        occurred_at=command.observed_at,
        payload={
            "usage_observation_id": str(row.id),
            "source_owner": row.source_owner,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_version": row.source_version,
            "relation": row.relation,
            "active": row.active,
        },
    )
    return row


def usage_references(
    db: Session, *, tenant_id: UUID, revision_id: UUID
) -> tuple[MediaUsageObservation, ...]:
    _revision(db, tenant_id, revision_id)
    rows = db.scalars(
        select(MediaUsageObservation).where(
            MediaUsageObservation.tenant_id == tenant_id,
            MediaUsageObservation.revision_id == revision_id,
        )
    ).all()
    latest: dict[
        tuple[str, str, str, str, str, UUID | None], MediaUsageObservation
    ] = {}
    for row in rows:
        key = (
            row.source_owner,
            row.source_type,
            row.source_id,
            row.source_version,
            row.relation,
            row.rendition_id,
        )
        previous = latest.get(key)
        if previous is None or (row.observed_at, str(row.id)) > (
            previous.observed_at,
            str(previous.id),
        ):
            latest[key] = row
    return tuple(
        sorted(
            (row for row in latest.values() if row.active),
            key=lambda row: (
                row.source_owner,
                row.source_type,
                row.source_id,
                row.source_version,
                row.relation,
                str(row.rendition_id or ""),
            ),
        )
    )


def evaluate_disposition(
    db: Session,
    *,
    tenant_id: UUID,
    revision_id: UUID,
    record: RecordDispositionObservation,
) -> DispositionDecision:
    revision = _revision(db, tenant_id, revision_id)
    asset = _asset(db, tenant_id, revision.asset_id)
    reasons: list[str] = []
    if usage_references(db, tenant_id=tenant_id, revision_id=revision.id):
        reasons.append("active_usage")
    if asset.current_revision_id == revision.id:
        reasons.append("current_revision")
    if record.declared_as_record and not record.records_allows_disposition:
        reasons.append("records_refused_disposition")
    return DispositionDecision(
        archive_allowed="records_refused_disposition" not in reasons,
        dispose_allowed=not reasons,
        reasons=tuple(sorted(reasons)),
        records_evidence_ref=record.evidence_ref,
    )


__all__ = [
    "add_annotation",
    "add_collection_item",
    "add_revision",
    "apply_rights_deadline",
    "assign_classification",
    "complete_rendition",
    "create_asset",
    "create_collection",
    "create_library",
    "evaluate_disposition",
    "evaluate_usage",
    "fail_rendition",
    "find_duplicate_revisions",
    "grant_access",
    "may_access",
    "observe_approval",
    "observe_scan",
    "observe_usage",
    "record_metadata_observation",
    "relate_revisions",
    "rendition_is_stale",
    "request_rendition",
    "request_rendition_regeneration",
    "resolve_annotation",
    "revoke_access",
    "save_selection",
    "set_rights",
    "stale_renditions",
    "transition_asset",
    "update_descriptive_metadata",
    "usage_references",
]
