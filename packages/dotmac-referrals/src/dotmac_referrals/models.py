"""Tenant-only referral persistence in ``mod_referrals``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("referrals")


def tenant_id_column() -> MappedColumn[UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class ReferralProgramme(Base, TimestampMixin):
    __tablename__ = "referral_programmes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_referral_programmes_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "code", name="uq_referral_programmes_tenant_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "active_version_id"],
            [
                f"{SCHEMA}.referral_programme_versions.tenant_id",
                f"{SCHEMA}.referral_programme_versions.id",
            ],
            name="fk_referral_programmes_active_version",
            use_alter=True,
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    qualification_policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reward_policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    active_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")


class ReferralProgrammeVersion(Base):
    __tablename__ = "referral_programme_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_referral_programme_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "programme_id",
            "version_number",
            name="uq_referral_programme_versions_tenant_programme_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                f"{SCHEMA}.referral_programmes.tenant_id",
                f"{SCHEMA}.referral_programmes.id",
            ],
            ondelete="CASCADE",
            name="fk_referral_programme_versions_programme",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    programme_id: Mapped[UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    qualification_policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reward_policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_referral_codes_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_referral_codes_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                f"{SCHEMA}.referral_programmes.tenant_id",
                f"{SCHEMA}.referral_programmes.id",
            ],
            ondelete="RESTRICT",
            name="fk_referral_codes_programme",
        ),
        Index("ix_referral_codes_tenant_programme", "tenant_id", "programme_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    programme_id: Mapped[UUID] = mapped_column(nullable=False)
    referrer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_referrals_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_referrals_tenant_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                f"{SCHEMA}.referral_programmes.tenant_id",
                f"{SCHEMA}.referral_programmes.id",
            ],
            ondelete="RESTRICT",
            name="fk_referrals_programme",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "code_id"],
            [f"{SCHEMA}.referral_codes.tenant_id", f"{SCHEMA}.referral_codes.id"],
            ondelete="RESTRICT",
            name="fk_referrals_code",
        ),
        Index("ix_referrals_tenant_subject", "tenant_id", "referred_subject_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    programme_id: Mapped[UUID] = mapped_column(nullable=False)
    code_id: Mapped[UUID] = mapped_column(nullable=False)
    referred_subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="attributed"
    )
    attributed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReferralConversion(Base):
    __tablename__ = "referral_conversions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_referral_conversions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "referral_id", name="uq_referral_conversions_tenant_referral"
        ),
        UniqueConstraint(
            "tenant_id", "conversion_ref", name="uq_referral_conversions_tenant_ref"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "referral_id"],
            [f"{SCHEMA}.referrals.tenant_id", f"{SCHEMA}.referrals.id"],
            ondelete="RESTRICT",
            name="fk_referral_conversions_referral",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    referral_id: Mapped[UUID] = mapped_column(nullable=False)
    conversion_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    qualification_evidence_digest: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    reward_request_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    outbox_event_id: Mapped[UUID] = mapped_column(nullable=False)
    converted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    ReferralProgramme,
    ReferralProgrammeVersion,
    ReferralCode,
    Referral,
    ReferralConversion,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "TABLES",
    "Referral",
    "ReferralCode",
    "ReferralConversion",
    "ReferralProgramme",
    "ReferralProgrammeVersion",
]
