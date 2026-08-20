"""Behavior canaries for immutable versions and Documents-owned lifecycle."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_documents import (
    AccessEffect,
    AccessPrincipal,
    AccessTarget,
    AddRendition,
    AddVersion,
    ApprovalVerdict,
    CheckoutConflict,
    CreateDocument,
    CreateDocumentTypeVersion,
    CreateLibrary,
    DocumentState,
    FileEvidence,
    GrantDocumentAccess,
    InvalidLifecycleTransition,
    RenditionKind,
    ScheduleDocumentTransition,
    SourceProvenance,
    VersionBump,
    VersionConflict,
    acquire_checkout,
    add_rendition,
    add_version,
    create_document,
    create_document_type_version,
    create_library,
    decide_document_access,
    grant_document_access,
    release_checkout,
    schedule_document_transition,
    transition_document,
)
from dotmac_documents.models import ALL_MODELS, DocumentEvent
from dotmac_kernel.models import Tenant
from dotmac_kernel.testing import create_test_engine, isolated_session
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def documents_engine():
    engine = create_test_engine(
        tables=(Tenant.__table__, *(model.__table__ for model in ALL_MODELS))
    )
    yield engine
    engine.dispose()


@pytest.fixture
def documents_db(documents_engine) -> Iterator[Session]:
    with isolated_session(documents_engine) as db:
        yield db


@pytest.fixture
def tenant_id(documents_db: Session) -> uuid.UUID:
    tenant = Tenant(slug=f"docs-{uuid.uuid4().hex[:8]}", name="Documents")
    documents_db.add(tenant)
    documents_db.flush()
    return tenant.id


def _seed_document(db: Session, tenant_id: uuid.UUID):
    actor = uuid.uuid4()
    library = create_library(
        db,
        tenant_id=tenant_id,
        command=CreateLibrary(code="controlled", name="Controlled"),
        actor_id=actor,
        recorded_at=NOW,
    )
    definition = create_document_type_version(
        db,
        tenant_id=tenant_id,
        command=CreateDocumentTypeVersion(
            type_code="policy",
            version=1,
            metadata_schema={"department": "string"},
            required_fields=("department",),
            allowed_transitions={
                "draft": ("in_review",),
                "in_review": ("approved",),
                "approved": ("effective",),
                "effective": ("superseded", "withdrawn", "archived"),
            },
            approval_required_states=("approved",),
            major_minor=True,
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    document = create_document(
        db,
        tenant_id=tenant_id,
        command=CreateDocument(
            library_id=library.id,
            type_code=definition.type_code,
            type_version=definition.version,
            code="POL-001",
            title="Information Security Policy",
            metadata={"department": "Technology"},
            folder_path="/policies/security",
            tags=("security",),
            sensitivity="internal",
            handling_instructions=("no-external-download",),
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    return document, actor


def _file(seed: str) -> FileEvidence:
    return FileEvidence(
        file_id=uuid.uuid4(),
        checksum_sha256=f"sha256:{seed * 64}",
        media_type="application/pdf",
        byte_length=1024,
    )


def test_versions_are_allocated_under_lock_and_advance_one_current_pointer(
    documents_db: Session, tenant_id: uuid.UUID
) -> None:
    document, actor = _seed_document(documents_db, tenant_id)
    first = add_version(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        command=AddVersion(
            file=_file("a"),
            provenance=SourceProvenance.UPLOAD,
            authored_by=actor,
            authored_at=NOW,
            change_reason="Initial controlled issue",
            bump=VersionBump.MAJOR,
            metadata={"department": "Technology"},
        ),
        recorded_at=NOW,
    )
    second = add_version(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        command=AddVersion(
            file=_file("b"),
            provenance=SourceProvenance.UPLOAD,
            authored_by=actor,
            authored_at=NOW + timedelta(minutes=1),
            change_reason="Correct contact details",
            bump=VersionBump.MINOR,
            expected_current_version_id=first.id,
            metadata={"department": "Technology"},
        ),
        recorded_at=NOW + timedelta(minutes=1),
    )
    assert (first.major_number, first.minor_number) == (1, 0)
    assert (second.major_number, second.minor_number) == (1, 1)
    assert document.current_version_id == second.id
    assert first.file_id != second.file_id

    with pytest.raises(VersionConflict):
        add_version(
            documents_db,
            tenant_id=tenant_id,
            document_id=document.id,
            command=AddVersion(
                file=_file("c"),
                provenance=SourceProvenance.API,
                authored_by=actor,
                authored_at=NOW,
                change_reason="Stale edit",
                bump=VersionBump.MINOR,
                expected_current_version_id=first.id,
                metadata={"department": "Technology"},
            ),
            recorded_at=NOW,
        )


def test_approval_is_an_observation_and_documents_performs_the_transition(
    documents_db: Session, tenant_id: uuid.UUID
) -> None:
    document, actor = _seed_document(documents_db, tenant_id)
    version = add_version(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        command=AddVersion(
            file=_file("d"),
            provenance=SourceProvenance.GENERATED,
            authored_by=actor,
            authored_at=NOW,
            change_reason="Issue for approval",
            bump=VersionBump.MAJOR,
            metadata={"department": "Technology"},
        ),
        recorded_at=NOW,
    )
    transition_document(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        target=DocumentState.IN_REVIEW,
        actor_id=actor,
        recorded_at=NOW,
    )
    with pytest.raises(VersionConflict, match="digest"):
        transition_document(
            documents_db,
            tenant_id=tenant_id,
            document_id=document.id,
            target=DocumentState.APPROVED,
            actor_id=actor,
            recorded_at=NOW,
            approval=ApprovalVerdict(
                request_id=uuid.uuid4(),
                subject_id=str(version.id),
                content_digest=f"sha256:{'e' * 64}",
                approved=True,
                decided_at=NOW,
            ),
        )
    transition_document(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        target=DocumentState.APPROVED,
        actor_id=actor,
        recorded_at=NOW,
        approval=ApprovalVerdict(
            request_id=uuid.uuid4(),
            subject_id=str(version.id),
            content_digest=version.checksum_sha256,
            approved=True,
            decided_at=NOW,
        ),
    )
    due_at = NOW + timedelta(days=1)
    with pytest.raises(InvalidLifecycleTransition, match="Durable Timers"):
        transition_document(
            documents_db,
            tenant_id=tenant_id,
            document_id=document.id,
            target=DocumentState.EFFECTIVE,
            actor_id=actor,
            recorded_at=NOW,
            effective_at=due_at,
        )
    timer = schedule_document_transition(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        command=ScheduleDocumentTransition(
            schedule_id=uuid.uuid4(),
            target=DocumentState.EFFECTIVE,
            due_at=due_at,
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    assert document.state == DocumentState.APPROVED
    assert timer.owner == "documents"
    assert timer.due_at == due_at
    transition_document(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        target=DocumentState.EFFECTIVE,
        actor_id=actor,
        recorded_at=due_at,
        effective_at=due_at,
        review_at=NOW + timedelta(days=365),
        expected_current_version_id=timer.source_version_id,
    )
    assert document.state == DocumentState.EFFECTIVE
    events = list(
        documents_db.scalars(
            select(DocumentEvent).where(DocumentEvent.document_id == document.id)
        )
    )
    assert any(event.event_type == "approval.observed" for event in events)
    assert any(event.event_type == "document.effective" for event in events)


def test_rendition_is_bound_to_the_exact_source_checksum(
    documents_db: Session, tenant_id: uuid.UUID
) -> None:
    document, actor = _seed_document(documents_db, tenant_id)
    version = add_version(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        command=AddVersion(
            file=_file("f"),
            provenance=SourceProvenance.SCAN,
            authored_by=actor,
            authored_at=NOW,
            change_reason="Scanned original",
            bump=VersionBump.MAJOR,
            metadata={"department": "Technology"},
        ),
        recorded_at=NOW,
    )
    with pytest.raises(VersionConflict, match="source checksum"):
        add_rendition(
            documents_db,
            tenant_id=tenant_id,
            command=AddRendition(
                source_version_id=version.id,
                source_checksum_sha256=f"sha256:{'0' * 64}",
                kind=RenditionKind.OCR_TEXT,
                output=_file("1"),
                renderer_code="tesseract",
                renderer_version="5.4.1",
            ),
            actor_id=actor,
            recorded_at=NOW,
        )
    rendition = add_rendition(
        documents_db,
        tenant_id=tenant_id,
        command=AddRendition(
            source_version_id=version.id,
            source_checksum_sha256=version.checksum_sha256,
            kind=RenditionKind.PREVIEW_PDF,
            output=_file("2"),
            renderer_code="libreoffice",
            renderer_version="24.2",
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    assert rendition.source_version_id == version.id
    assert rendition.file_id != version.file_id


def test_checkout_is_exclusive_until_expiry_or_explicit_release(
    documents_db: Session, tenant_id: uuid.UUID
) -> None:
    document, actor = _seed_document(documents_db, tenant_id)
    other = uuid.uuid4()
    lease = acquire_checkout(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        owner_id=actor,
        expires_at=NOW + timedelta(hours=1),
        recorded_at=NOW,
    )
    with pytest.raises(CheckoutConflict):
        acquire_checkout(
            documents_db,
            tenant_id=tenant_id,
            document_id=document.id,
            owner_id=other,
            expires_at=NOW + timedelta(hours=1),
            recorded_at=NOW + timedelta(minutes=1),
        )
    release_checkout(
        documents_db,
        tenant_id=tenant_id,
        checkout_id=lease.id,
        actor_id=actor,
        recorded_at=NOW + timedelta(minutes=2),
    )
    replacement = acquire_checkout(
        documents_db,
        tenant_id=tenant_id,
        document_id=document.id,
        owner_id=other,
        expires_at=NOW + timedelta(hours=2),
        recorded_at=NOW + timedelta(minutes=3),
    )
    assert replacement.owner_id == other


def test_access_resolution_inherits_and_same_target_deny_wins(
    documents_db: Session, tenant_id: uuid.UUID
) -> None:
    document, actor = _seed_document(documents_db, tenant_id)
    principal = AccessPrincipal(kind="user", ref="party:reader")
    targets = (
        AccessTarget(kind="library", ref=str(document.library_id)),
        AccessTarget(kind="document", ref=str(document.id)),
    )
    grant_document_access(
        documents_db,
        tenant_id=tenant_id,
        command=GrantDocumentAccess(
            target_kind="library",
            target_ref=str(document.library_id),
            principal_kind=principal.kind,
            principal_ref=principal.ref,
            actions=("read",),
            effect=AccessEffect.ALLOW,
            inherits=True,
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    inherited = decide_document_access(
        documents_db,
        tenant_id=tenant_id,
        principals=(principal,),
        action="read",
        target_chain=targets,
        evaluated_at=NOW,
    )
    assert inherited.allowed is True

    grant_document_access(
        documents_db,
        tenant_id=tenant_id,
        command=GrantDocumentAccess(
            target_kind="document",
            target_ref=str(document.id),
            principal_kind=principal.kind,
            principal_ref=principal.ref,
            actions=("read",),
            effect=AccessEffect.DENY,
        ),
        actor_id=actor,
        recorded_at=NOW,
    )
    denied = decide_document_access(
        documents_db,
        tenant_id=tenant_id,
        principals=(principal,),
        action="read",
        target_chain=targets,
        evaluated_at=NOW,
    )
    assert denied.allowed is False
    assert denied.effect is AccessEffect.DENY
