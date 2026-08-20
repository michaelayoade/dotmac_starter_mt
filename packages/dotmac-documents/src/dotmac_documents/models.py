"""Tenant-only controlled document persistence in ``mod_documents``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("documents")


def _tenant_id() -> MappedColumn[uuid.UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class DocumentLibrary(Base):
    __tablename__ = "document_libraries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_libraries_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_document_libraries_tenant_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentTypeVersion(Base):
    __tablename__ = "document_type_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_document_type_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "type_code",
            "version",
            name="uq_document_type_versions_code_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    metadata_schema: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    required_fields: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    allowed_transitions: Mapped[dict[str, list[str]]] = mapped_column(
        JSON(), nullable=False
    )
    approval_required_states: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    major_minor: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "library_id", "code", name="uq_documents_library_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                f"{SCHEMA}.document_libraries.tenant_id",
                f"{SCHEMA}.document_libraries.id",
            ],
            ondelete="RESTRICT",
            name="fk_documents_library",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "type_code", "type_version"],
            [
                f"{SCHEMA}.document_type_versions.tenant_id",
                f"{SCHEMA}.document_type_versions.type_code",
                f"{SCHEMA}.document_type_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_documents_type_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_version_id"],
            [
                f"{SCHEMA}.document_versions.tenant_id",
                f"{SCHEMA}.document_versions.document_id",
                f"{SCHEMA}.document_versions.id",
            ],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_documents_current_version",
        ),
        Index("ix_documents_tenant_state", "tenant_id", "state"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    library_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    type_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    document_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON(), nullable=False
    )
    tags: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(80), nullable=False)
    handling_instructions: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "ordinal",
            name="uq_document_versions_document_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "id",
            name="uq_document_versions_tenant_document_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "major_number",
            "minor_number",
            name="uq_document_versions_semantic_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="RESTRICT",
            name="fk_document_versions_document",
        ),
        Index("ix_document_versions_document", "tenant_id", "document_id", "ordinal"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer(), nullable=False)
    major_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    minor_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    authored_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    authored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    change_reason: Mapped[str] = mapped_column(Text(), nullable=False)
    version_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentRendition(Base):
    __tablename__ = "document_renditions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_renditions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_version_id",
            "kind",
            "renderer_code",
            "renderer_version",
            "output_checksum_sha256",
            name="uq_document_renditions_exact_output",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_version_id"],
            [f"{SCHEMA}.document_versions.tenant_id", f"{SCHEMA}.document_versions.id"],
            ondelete="RESTRICT",
            name="fk_document_renditions_source_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    source_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_checksum_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    output_checksum_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    renderer_code: Mapped[str] = mapped_column(String(120), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentClassification(Base):
    __tablename__ = "document_classifications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_document_classifications_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "taxonomy_code",
            "value_code",
            name="uq_document_classifications_value",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="CASCADE",
            name="fk_document_classifications_document",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    taxonomy_code: Mapped[str] = mapped_column(String(120), nullable=False)
    value_code: Mapped[str] = mapped_column(String(120), nullable=False)
    hierarchy_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentRelation(Base):
    __tablename__ = "document_relations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_relations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_edge",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="CASCADE",
            name="fk_document_relations_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="CASCADE",
            name="fk_document_relations_target",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    source_document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentCheckout(Base):
    __tablename__ = "document_checkouts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_checkouts_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="CASCADE",
            name="fk_document_checkouts_document",
        ),
        Index(
            "ix_document_checkouts_active", "tenant_id", "document_id", "released_at"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[uuid.UUID | None] = mapped_column()
    release_reason: Mapped[str | None] = mapped_column(String(500))
    break_glass: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)


class DocumentAnnotation(Base):
    __tablename__ = "document_annotations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_document_annotations_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [f"{SCHEMA}.document_versions.tenant_id", f"{SCHEMA}.document_versions.id"],
            ondelete="CASCADE",
            name="fk_document_annotations_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    principal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    anchor: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    finding_code: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column()


class DocumentAccessGrant(Base):
    __tablename__ = "document_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_document_access_grants_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "target_kind",
            "target_ref",
            "principal_kind",
            "principal_ref",
            "effect",
            name="uq_document_access_grants_rule",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(1000), nullable=False)
    principal_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    actions: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    inherits: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentAcknowledgement(Base):
    __tablename__ = "document_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_document_acknowledgements_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "principal_ref",
            name="uq_document_acknowledgements_principal_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [f"{SCHEMA}.document_versions.tenant_id", f"{SCHEMA}.document_versions.id"],
            ondelete="RESTRICT",
            name="fk_document_acknowledgements_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    principal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    attestation_text: Mapped[str] = mapped_column(Text(), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentEvent(Base):
    __tablename__ = "document_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_events_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            ondelete="RESTRICT",
            name="fk_document_events_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "version_id"],
            [
                f"{SCHEMA}.document_versions.tenant_id",
                f"{SCHEMA}.document_versions.document_id",
                f"{SCHEMA}.document_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_document_events_version",
        ),
        Index("ix_document_events_timeline", "tenant_id", "document_id", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version_id: Mapped[uuid.UUID | None] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    DocumentLibrary,
    DocumentTypeVersion,
    Document,
    DocumentVersion,
    DocumentRendition,
    DocumentClassification,
    DocumentRelation,
    DocumentCheckout,
    DocumentAnnotation,
    DocumentAccessGrant,
    DocumentAcknowledgement,
    DocumentEvent,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "TABLES",
    "Document",
    "DocumentAccessGrant",
    "DocumentAcknowledgement",
    "DocumentAnnotation",
    "DocumentCheckout",
    "DocumentClassification",
    "DocumentEvent",
    "DocumentLibrary",
    "DocumentRelation",
    "DocumentRendition",
    "DocumentTypeVersion",
    "DocumentVersion",
    "SCHEMA",
]
