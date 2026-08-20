"""Documents-owned decisions in the caller's transaction.

Every query is tenant-scoped even though PostgreSQL RLS enforces the same
boundary. Services mutate and flush; they never create sessions, commit,
rollback, fetch bytes, call approval providers or scan the clock.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_documents.contracts import (
    DOCUMENT_ACTIONS,
    AccessEffect,
    AccessPrincipal,
    AccessTarget,
    AcknowledgeVersion,
    AddAnnotation,
    AddRendition,
    AddVersion,
    ApprovalVerdict,
    CheckoutConflict,
    CreateDocument,
    CreateDocumentTypeVersion,
    CreateLibrary,
    DocumentAccessDecision,
    DocumentError,
    DocumentNotFound,
    DocumentState,
    DocumentTimerRequest,
    GrantDocumentAccess,
    InvalidLifecycleTransition,
    MetadataInvalid,
    RelationKind,
    ScheduleDocumentTransition,
    VersionBump,
    VersionConflict,
)
from dotmac_documents.models import (
    Document,
    DocumentAccessGrant,
    DocumentAcknowledgement,
    DocumentAnnotation,
    DocumentCheckout,
    DocumentClassification,
    DocumentEvent,
    DocumentLibrary,
    DocumentRelation,
    DocumentRendition,
    DocumentTypeVersion,
    DocumentVersion,
)


def _library(db: Session, tenant_id: UUID, library_id: UUID) -> DocumentLibrary:
    row = db.scalar(
        select(DocumentLibrary).where(
            DocumentLibrary.tenant_id == tenant_id, DocumentLibrary.id == library_id
        )
    )
    if row is None:
        raise DocumentNotFound(f"document library {library_id} was not found")
    return row


def _type_version(
    db: Session, tenant_id: UUID, type_code: str, version: int
) -> DocumentTypeVersion:
    row = db.scalar(
        select(DocumentTypeVersion).where(
            DocumentTypeVersion.tenant_id == tenant_id,
            DocumentTypeVersion.type_code == type_code,
            DocumentTypeVersion.version == version,
        )
    )
    if row is None:
        raise DocumentNotFound(
            f"document type {type_code!r} version {version} was not found"
        )
    return row


def _document(
    db: Session, tenant_id: UUID, document_id: UUID, *, lock: bool = False
) -> Document:
    statement = select(Document).where(
        Document.tenant_id == tenant_id, Document.id == document_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise DocumentNotFound(f"document {document_id} was not found")
    return row


def _version(db: Session, tenant_id: UUID, version_id: UUID) -> DocumentVersion:
    row = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == version_id
        )
    )
    if row is None:
        raise DocumentNotFound(f"document version {version_id} was not found")
    return row


def _validate_metadata(
    definition: DocumentTypeVersion, metadata: dict[str, object]
) -> None:
    missing = [field for field in definition.required_fields if field not in metadata]
    if missing:
        raise MetadataInvalid(
            f"required document metadata is missing: {', '.join(missing)}"
        )


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    version_id: UUID | None,
    event_type: str,
    actor_id: UUID | None,
    payload: dict[str, object],
    occurred_at: datetime,
    event_id: UUID | None = None,
) -> DocumentEvent:
    row = DocumentEvent(
        id=event_id,
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=version_id,
        event_type=event_type,
        actor_id=actor_id,
        payload=payload,
        occurred_at=occurred_at,
    )
    db.add(row)
    return row


def create_library(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateLibrary,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentLibrary:
    row = DocumentLibrary(
        tenant_id=tenant_id,
        code=command.code,
        name=command.name,
        description=command.description,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def create_document_type_version(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateDocumentTypeVersion,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentTypeVersion:
    row = DocumentTypeVersion(
        tenant_id=tenant_id,
        type_code=command.type_code,
        version=command.version,
        metadata_schema=dict(command.metadata_schema),
        required_fields=list(command.required_fields),
        allowed_transitions={
            key: list(value) for key, value in command.allowed_transitions.items()
        },
        approval_required_states=list(command.approval_required_states),
        major_minor=command.major_minor,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def create_document(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateDocument,
    actor_id: UUID,
    recorded_at: datetime,
) -> Document:
    _library(db, tenant_id, command.library_id)
    definition = _type_version(db, tenant_id, command.type_code, command.type_version)
    _validate_metadata(definition, command.metadata)
    row = Document(
        tenant_id=tenant_id,
        library_id=command.library_id,
        type_code=command.type_code,
        type_version=command.type_version,
        code=command.code,
        title=command.title,
        folder_path=command.folder_path,
        state=DocumentState.DRAFT.value,
        current_version_id=None,
        document_metadata=dict(command.metadata),
        tags=list(command.tags),
        sensitivity=command.sensitivity,
        handling_instructions=list(command.handling_instructions),
        created_by=actor_id,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        document_id=row.id,
        version_id=None,
        event_type="document.created",
        actor_id=actor_id,
        payload={
            "code": row.code,
            "type_code": row.type_code,
            "type_version": row.type_version,
        },
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def add_version(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    command: AddVersion,
    recorded_at: datetime,
) -> DocumentVersion:
    document = _document(db, tenant_id, document_id, lock=True)
    definition = _type_version(db, tenant_id, document.type_code, document.type_version)
    _validate_metadata(definition, command.metadata)
    if command.expected_current_version_id != document.current_version_id and (
        command.expected_current_version_id is not None
        or document.current_version_id is not None
    ):
        raise VersionConflict(
            "the document current version changed after editing began"
        )

    prior: DocumentVersion | None = None
    if document.current_version_id is not None:
        prior = _version(db, tenant_id, document.current_version_id)
    if prior is None:
        ordinal, major, minor = 1, 1, 0
    elif command.bump is VersionBump.MAJOR:
        ordinal, major, minor = prior.ordinal + 1, prior.major_number + 1, 0
    else:
        if not definition.major_minor:
            raise VersionConflict("this document type does not permit minor versions")
        ordinal, major, minor = (
            prior.ordinal + 1,
            prior.major_number,
            prior.minor_number + 1,
        )

    row = DocumentVersion(
        tenant_id=tenant_id,
        document_id=document.id,
        ordinal=ordinal,
        major_number=major,
        minor_number=minor,
        file_id=command.file.file_id,
        checksum_sha256=command.file.checksum_sha256,
        media_type=command.file.media_type,
        byte_length=command.file.byte_length,
        provenance=command.provenance.value,
        authored_by=command.authored_by,
        authored_at=command.authored_at,
        change_reason=command.change_reason,
        version_metadata=dict(command.metadata),
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    document.current_version_id = row.id
    document.document_metadata = dict(command.metadata)
    document.updated_at = recorded_at
    _event(
        db,
        tenant_id=tenant_id,
        document_id=document.id,
        version_id=row.id,
        event_type="document.version_created",
        actor_id=command.authored_by,
        payload={
            "ordinal": ordinal,
            "version": f"{major}.{minor}",
            "file_id": str(row.file_id),
            "checksum_sha256": row.checksum_sha256,
            "provenance": row.provenance,
        },
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def transition_document(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    target: DocumentState,
    actor_id: UUID,
    recorded_at: datetime,
    effective_at: datetime | None = None,
    review_at: datetime | None = None,
    approval: ApprovalVerdict | None = None,
    expected_current_version_id: UUID | None = None,
) -> Document:
    document = _document(db, tenant_id, document_id, lock=True)
    definition = _type_version(db, tenant_id, document.type_code, document.type_version)
    allowed = definition.allowed_transitions.get(document.state, [])
    if target.value not in allowed:
        raise InvalidLifecycleTransition(
            f"{document.state} cannot transition to {target.value}"
        )
    current = (
        _version(db, tenant_id, document.current_version_id)
        if document.current_version_id is not None
        else None
    )
    if current is None:
        raise VersionConflict(
            "a document needs an exact current version before lifecycle transition"
        )
    if (
        expected_current_version_id is not None
        and current.id != expected_current_version_id
    ):
        raise VersionConflict(
            "scheduled transition no longer identifies the current version"
        )
    if target.value in definition.approval_required_states:
        if approval is None or not approval.approved:
            raise InvalidLifecycleTransition(
                "an approved exact-content verdict is required"
            )
        if approval.subject_id != str(current.id):
            raise VersionConflict(
                "approval subject does not identify the current version"
            )
        if approval.content_digest != current.checksum_sha256:
            raise VersionConflict(
                "approval digest does not match the current version digest"
            )
        _event(
            db,
            tenant_id=tenant_id,
            document_id=document.id,
            version_id=current.id,
            event_type="approval.observed",
            actor_id=actor_id,
            payload={
                "request_id": str(approval.request_id),
                "content_digest": approval.content_digest,
                "decided_at": approval.decided_at.isoformat(),
            },
            occurred_at=recorded_at,
        )
    if target is DocumentState.EFFECTIVE and effective_at is None:
        raise InvalidLifecycleTransition("effective_at is an explicit required input")
    if effective_at is not None and (
        effective_at.tzinfo is None or effective_at.utcoffset() is None
    ):
        raise InvalidLifecycleTransition("effective_at must be timezone-aware")
    if review_at is not None and (
        review_at.tzinfo is None or review_at.utcoffset() is None
    ):
        raise InvalidLifecycleTransition("review_at must be timezone-aware")
    if (
        target is DocumentState.EFFECTIVE
        and effective_at is not None
        and effective_at > recorded_at
    ):
        raise InvalidLifecycleTransition(
            "a future effective transition must be scheduled through Durable Timers"
        )
    document.state = target.value
    document.effective_at = (
        effective_at if target is DocumentState.EFFECTIVE else document.effective_at
    )
    document.review_at = review_at if review_at is not None else document.review_at
    document.updated_at = recorded_at
    _event(
        db,
        tenant_id=tenant_id,
        document_id=document.id,
        version_id=current.id,
        event_type=f"document.{target.value}",
        actor_id=actor_id,
        payload={
            "effective_at": effective_at.isoformat() if effective_at else None,
            "review_at": review_at.isoformat() if review_at else None,
        },
        occurred_at=recorded_at,
    )
    db.flush()
    return document


def schedule_document_transition(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    command: ScheduleDocumentTransition,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentTimerRequest:
    """Record scheduled intent and return a provider-neutral Durable Timer request."""

    if command.due_at <= recorded_at:
        raise InvalidLifecycleTransition("a scheduled transition must be in the future")
    document = _document(db, tenant_id, document_id, lock=True)
    definition = _type_version(db, tenant_id, document.type_code, document.type_version)
    allowed = definition.allowed_transitions.get(document.state, [])
    if command.target.value not in allowed:
        raise InvalidLifecycleTransition(
            f"{document.state} cannot transition to {command.target.value}"
        )
    if document.current_version_id is None:
        raise VersionConflict(
            "a scheduled transition must bind an exact current version"
        )
    current = _version(db, tenant_id, document.current_version_id)
    payload: dict[str, object] = {
        "target": command.target.value,
        "due_at": command.due_at.isoformat(),
        "source_version_id": str(current.id),
        "source_checksum_sha256": current.checksum_sha256,
    }
    existing = db.scalar(
        select(DocumentEvent).where(
            DocumentEvent.tenant_id == tenant_id,
            DocumentEvent.id == command.schedule_id,
        )
    )
    if existing is not None:
        if (
            existing.document_id != document.id
            or existing.event_type != "document.transition_scheduled"
            or existing.payload != payload
        ):
            raise VersionConflict(
                "schedule identity was reused with different transition evidence"
            )
    else:
        _event(
            db,
            tenant_id=tenant_id,
            document_id=document.id,
            version_id=current.id,
            event_type="document.transition_scheduled",
            actor_id=actor_id,
            payload=payload,
            occurred_at=recorded_at,
            event_id=command.schedule_id,
        )
        db.flush()
    return DocumentTimerRequest(
        owner="documents",
        entity_kind="document",
        entity_id=str(document.id),
        purpose=f"transition:{command.target.value}",
        due_at=command.due_at,
        source_version_id=current.id,
        source_checksum_sha256=current.checksum_sha256,
    )


def add_rendition(
    db: Session,
    *,
    tenant_id: UUID,
    command: AddRendition,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentRendition:
    source = _version(db, tenant_id, command.source_version_id)
    if source.checksum_sha256 != command.source_checksum_sha256:
        raise VersionConflict(
            "rendition source checksum does not match the stored source version"
        )
    row = DocumentRendition(
        tenant_id=tenant_id,
        source_version_id=source.id,
        kind=command.kind.value,
        source_checksum_sha256=source.checksum_sha256,
        file_id=command.output.file_id,
        output_checksum_sha256=command.output.checksum_sha256,
        media_type=command.output.media_type,
        byte_length=command.output.byte_length,
        renderer_code=command.renderer_code,
        renderer_version=command.renderer_version,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    _event(
        db,
        tenant_id=tenant_id,
        document_id=source.document_id,
        version_id=source.id,
        event_type="document.rendition_created",
        actor_id=actor_id,
        payload={
            "kind": row.kind,
            "file_id": str(row.file_id),
            "checksum_sha256": row.output_checksum_sha256,
        },
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def classify_document(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    taxonomy_code: str,
    value_code: str,
    hierarchy_path: str,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentClassification:
    _document(db, tenant_id, document_id)
    row = DocumentClassification(
        tenant_id=tenant_id,
        document_id=document_id,
        taxonomy_code=taxonomy_code,
        value_code=value_code,
        hierarchy_path=hierarchy_path,
        assigned_by=actor_id,
        assigned_at=recorded_at,
    )
    db.add(row)
    _event(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=None,
        event_type="document.classified",
        actor_id=actor_id,
        payload={"taxonomy_code": taxonomy_code, "value_code": value_code},
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def relate_documents(
    db: Session,
    *,
    tenant_id: UUID,
    source_document_id: UUID,
    target_document_id: UUID,
    kind: RelationKind,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentRelation:
    _document(db, tenant_id, source_document_id)
    _document(db, tenant_id, target_document_id)
    if source_document_id == target_document_id:
        raise DocumentError("a document cannot relate to itself")
    row = DocumentRelation(
        tenant_id=tenant_id,
        source_document_id=source_document_id,
        target_document_id=target_document_id,
        relation_type=kind.value,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def acquire_checkout(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    owner_id: UUID,
    expires_at: datetime,
    recorded_at: datetime,
) -> DocumentCheckout:
    _document(db, tenant_id, document_id, lock=True)
    if expires_at <= recorded_at:
        raise CheckoutConflict("checkout expiry must be after acquisition")
    active = db.scalar(
        select(DocumentCheckout)
        .where(
            DocumentCheckout.tenant_id == tenant_id,
            DocumentCheckout.document_id == document_id,
            DocumentCheckout.released_at.is_(None),
        )
        .order_by(DocumentCheckout.acquired_at.desc())
        .limit(1)
    )
    if active is not None and active.expires_at > recorded_at:
        raise CheckoutConflict(f"document is checked out by {active.owner_id}")
    if active is not None:
        active.released_at = recorded_at
        active.released_by = None
        active.release_reason = "expired"
        _event(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=None,
            event_type="document.checkout_expired",
            actor_id=None,
            payload={"checkout_id": str(active.id)},
            occurred_at=recorded_at,
        )
    row = DocumentCheckout(
        tenant_id=tenant_id,
        document_id=document_id,
        owner_id=owner_id,
        acquired_at=recorded_at,
        expires_at=expires_at,
        break_glass=False,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=None,
        event_type="document.checkout_acquired",
        actor_id=owner_id,
        payload={"checkout_id": str(row.id), "expires_at": expires_at.isoformat()},
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def renew_checkout(
    db: Session,
    *,
    tenant_id: UUID,
    checkout_id: UUID,
    owner_id: UUID,
    expires_at: datetime,
    recorded_at: datetime,
) -> DocumentCheckout:
    row = db.scalar(
        select(DocumentCheckout)
        .where(
            DocumentCheckout.tenant_id == tenant_id, DocumentCheckout.id == checkout_id
        )
        .with_for_update()
    )
    if row is None or row.released_at is not None or row.owner_id != owner_id:
        raise CheckoutConflict("checkout cannot be renewed by this principal")
    if row.expires_at <= recorded_at or expires_at <= recorded_at:
        raise CheckoutConflict("an expired checkout cannot be renewed")
    row.renewed_at = recorded_at
    row.expires_at = expires_at
    _event(
        db,
        tenant_id=tenant_id,
        document_id=row.document_id,
        version_id=None,
        event_type="document.checkout_renewed",
        actor_id=owner_id,
        payload={"checkout_id": str(row.id), "expires_at": expires_at.isoformat()},
        occurred_at=recorded_at,
    )
    db.flush()
    return row


def release_checkout(
    db: Session,
    *,
    tenant_id: UUID,
    checkout_id: UUID,
    actor_id: UUID,
    recorded_at: datetime,
    reason: str = "released",
    break_glass: bool = False,
) -> DocumentCheckout:
    row = db.scalar(
        select(DocumentCheckout)
        .where(
            DocumentCheckout.tenant_id == tenant_id, DocumentCheckout.id == checkout_id
        )
        .with_for_update()
    )
    if row is None:
        raise DocumentNotFound(f"checkout {checkout_id} was not found")
    if row.released_at is None:
        if actor_id != row.owner_id and not break_glass:
            raise CheckoutConflict(
                "only the checkout owner or break-glass admin may release it"
            )
        row.released_at = recorded_at
        row.released_by = actor_id
        row.release_reason = reason
        row.break_glass = break_glass
        _event(
            db,
            tenant_id=tenant_id,
            document_id=row.document_id,
            version_id=None,
            event_type=(
                "document.checkout_broken"
                if break_glass
                else "document.checkout_released"
            ),
            actor_id=actor_id,
            payload={"checkout_id": str(row.id), "reason": reason},
            occurred_at=recorded_at,
        )
        db.flush()
    return row


def add_annotation(
    db: Session,
    *,
    tenant_id: UUID,
    command: AddAnnotation,
    recorded_at: datetime,
) -> DocumentAnnotation:
    _version(db, tenant_id, command.version_id)
    row = DocumentAnnotation(
        tenant_id=tenant_id,
        version_id=command.version_id,
        principal_ref=command.principal_ref,
        body=command.body,
        anchor=dict(command.anchor),
        finding_code=command.finding_code,
        status="open",
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def resolve_annotation(
    db: Session,
    *,
    tenant_id: UUID,
    annotation_id: UUID,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentAnnotation:
    row = db.scalar(
        select(DocumentAnnotation)
        .where(
            DocumentAnnotation.tenant_id == tenant_id,
            DocumentAnnotation.id == annotation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DocumentNotFound(f"annotation {annotation_id} was not found")
    row.status = "resolved"
    row.resolved_by = actor_id
    row.resolved_at = recorded_at
    db.flush()
    return row


def grant_document_access(
    db: Session,
    *,
    tenant_id: UUID,
    command: GrantDocumentAccess,
    actor_id: UUID,
    recorded_at: datetime,
) -> DocumentAccessGrant:
    row = DocumentAccessGrant(
        tenant_id=tenant_id,
        target_kind=command.target_kind,
        target_ref=command.target_ref,
        principal_kind=command.principal_kind,
        principal_ref=command.principal_ref,
        actions=list(command.actions),
        effect=command.effect.value,
        inherits=command.inherits,
        expires_at=command.expires_at,
        created_by=actor_id,
        created_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def decide_document_access(
    db: Session,
    *,
    tenant_id: UUID,
    principals: tuple[AccessPrincipal, ...],
    action: str,
    target_chain: tuple[AccessTarget, ...],
    evaluated_at: datetime,
) -> DocumentAccessDecision:
    """Resolve grants from library/folder/document, with most-specific override.

    At one target, deny wins. A grant applies below its target only when
    ``inherits`` is true; a more-specific target may explicitly override the
    inherited result.
    """

    if action not in DOCUMENT_ACTIONS:
        raise DocumentError(f"unknown document action {action!r}")
    if not target_chain:
        raise DocumentError("access evaluation requires a target chain")
    principal_keys = {(principal.kind, principal.ref) for principal in principals}
    selected_effect: AccessEffect | None = None
    selected_ids: tuple[UUID, ...] = ()
    last_depth = len(target_chain) - 1
    for depth, target in enumerate(target_chain):
        candidates = list(
            db.scalars(
                select(DocumentAccessGrant).where(
                    DocumentAccessGrant.tenant_id == tenant_id,
                    DocumentAccessGrant.target_kind == target.kind,
                    DocumentAccessGrant.target_ref == target.ref,
                )
            )
        )
        applicable = [
            grant
            for grant in candidates
            if (grant.principal_kind, grant.principal_ref) in principal_keys
            and action in grant.actions
            and (depth == last_depth or grant.inherits)
            and (grant.expires_at is None or grant.expires_at > evaluated_at)
        ]
        if not applicable:
            continue
        denied = tuple(
            grant.id for grant in applicable if grant.effect == AccessEffect.DENY.value
        )
        if denied:
            selected_effect = AccessEffect.DENY
            selected_ids = denied
        else:
            selected_effect = AccessEffect.ALLOW
            selected_ids = tuple(grant.id for grant in applicable)
    return DocumentAccessDecision(
        allowed=selected_effect is AccessEffect.ALLOW,
        effect=selected_effect,
        matched_grant_ids=selected_ids,
    )


def acknowledge_version(
    db: Session,
    *,
    tenant_id: UUID,
    command: AcknowledgeVersion,
    acknowledged_at: datetime,
) -> DocumentAcknowledgement:
    version = _version(db, tenant_id, command.version_id)
    existing = db.scalar(
        select(DocumentAcknowledgement).where(
            DocumentAcknowledgement.tenant_id == tenant_id,
            DocumentAcknowledgement.version_id == command.version_id,
            DocumentAcknowledgement.principal_ref == command.principal_ref,
        )
    )
    if existing is not None:
        return existing
    row = DocumentAcknowledgement(
        tenant_id=tenant_id,
        version_id=command.version_id,
        principal_ref=command.principal_ref,
        attestation_text=command.attestation_text,
        evidence=dict(command.evidence),
        acknowledged_at=acknowledged_at,
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tenant_id=tenant_id,
        document_id=version.document_id,
        version_id=version.id,
        event_type="document.version_acknowledged",
        actor_id=None,
        payload={
            "acknowledgement_id": str(row.id),
            "principal_ref": command.principal_ref,
        },
        occurred_at=acknowledged_at,
    )
    db.flush()
    return row


__all__ = [
    "acknowledge_version",
    "acquire_checkout",
    "add_annotation",
    "add_rendition",
    "add_version",
    "classify_document",
    "create_document",
    "create_document_type_version",
    "create_library",
    "decide_document_access",
    "grant_document_access",
    "relate_documents",
    "release_checkout",
    "renew_checkout",
    "resolve_annotation",
    "schedule_document_transition",
    "transition_document",
]
