"""Tenant-only AI policy, operation, attempt and advisory evidence."""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import JSON, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("aiops")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False)


class AIPolicy(Base, TimestampMixin):
    __tablename__ = "ai_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_ai_policies_tenant_id_id"), UniqueConstraint("tenant_id", "code", name="uq_ai_policies_code"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); code: Mapped[str] = mapped_column(String(120), nullable=False); title: Mapped[str] = mapped_column(String(240), nullable=False); active: Mapped[bool] = mapped_column(nullable=False, default=True)


class AIPolicyVersion(Base):
    __tablename__ = "ai_policy_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_ai_policy_versions_tenant_id_id"), UniqueConstraint("tenant_id", "policy_id", "version", name="uq_ai_policy_versions_version"), ForeignKeyConstraint(["tenant_id", "policy_id"], [f"{SCHEMA}.ai_policies.tenant_id", f"{SCHEMA}.ai_policies.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); version: Mapped[int] = mapped_column(Integer, nullable=False); allowed_operation_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False); input_contract_ref: Mapped[str] = mapped_column(String(240), nullable=False); policy_digest: Mapped[str] = mapped_column(String(64), nullable=False); active: Mapped[bool] = mapped_column(nullable=False, default=False); published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIOperation(Base, TimestampMixin):
    __tablename__ = "ai_operations"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_ai_operations_tenant_id_id"), UniqueConstraint("tenant_id", "operation_key", name="uq_ai_operations_key"), ForeignKeyConstraint(["tenant_id", "policy_version_id"], [f"{SCHEMA}.ai_policy_versions.tenant_id", f"{SCHEMA}.ai_policy_versions.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); operation_key: Mapped[str] = mapped_column(String(200), nullable=False); request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False); policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); operation_kind: Mapped[str] = mapped_column(String(80), nullable=False); input_ref: Mapped[str] = mapped_column(String(240), nullable=False); input_digest: Mapped[str] = mapped_column(String(64), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending"); started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIExecutionAttempt(Base):
    __tablename__ = "ai_execution_attempts"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_ai_execution_attempts_tenant_id_id"), UniqueConstraint("tenant_id", "attempt_key", name="uq_ai_execution_attempts_key"), ForeignKeyConstraint(["tenant_id", "operation_id"], [f"{SCHEMA}.ai_operations.tenant_id", f"{SCHEMA}.ai_operations.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); operation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); attempt_key: Mapped[str] = mapped_column(String(200), nullable=False); observation_digest: Mapped[str] = mapped_column(String(64), nullable=False); outcome: Mapped[str] = mapped_column(String(24), nullable=False); output_ref: Mapped[str | None] = mapped_column(String(240)); output_digest: Mapped[str | None] = mapped_column(String(64)); provider_observation: Mapped[str | None] = mapped_column(String(160)); model_observation: Mapped[str | None] = mapped_column(String(160)); request_observation: Mapped[str | None] = mapped_column(String(200)); error_code: Mapped[str | None] = mapped_column(String(120)); observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIInsight(Base, TimestampMixin):
    __tablename__ = "ai_insights"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_ai_insights_tenant_id_id"), UniqueConstraint("tenant_id", "insight_key", name="uq_ai_insights_key"), ForeignKeyConstraint(["tenant_id", "operation_id"], [f"{SCHEMA}.ai_operations.tenant_id", f"{SCHEMA}.ai_operations.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); operation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); insight_key: Mapped[str] = mapped_column(String(200), nullable=False); insight_type: Mapped[str] = mapped_column(String(120), nullable=False); advisory_value: Mapped[str] = mapped_column(Text, nullable=False); confidence: Mapped[float | None] = mapped_column(Float); source_output_digest: Mapped[str] = mapped_column(String(64), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="advisory"); acknowledged_by_ref: Mapped[str | None] = mapped_column(String(200)); acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); action_evidence_ref: Mapped[str | None] = mapped_column(String(240))


TENANT_MODELS = (AIPolicy, AIPolicyVersion, AIOperation, AIExecutionAttempt, AIInsight)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)
__all__ = ["AIExecutionAttempt", "AIInsight", "AIOperation", "AIPolicy", "AIPolicyVersion", "SCHEMA", "TENANT_MODELS", "TENANT_TABLES"]
