"""Feature-flag override storage, and the version that invalidates its cache.

Tenancy follows `domain_settings`, the one documented exception to hard rule 11:
`tenant_id` is NULLABLE, and `tenant_id IS NULL` means a DEPLOYMENT-scope row
readable by every tenant but writable by none of them. That shape is deliberate
here for the same reason it is there — a flag genuinely has two scopes, and
modelling the platform scope as a separate table would duplicate every column
and every query to express one nullable discriminator.

Uniqueness needs two PARTIAL indexes rather than one composite constraint,
because Postgres treats NULL as distinct from every other NULL: without the
partial index, any number of `tenant_id IS NULL` rows could collide on the same
flag code. Same defence `domain_settings` documents.

`override_version` is the cache's invalidation handle: it is derived from the
override rows themselves, so any write changes it and every cached evaluation of
the previous generation becomes unreachable. Explicit versioning rather than a
TTL, because "how stale can a kill switch be?" has only one acceptable answer.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column

from dotmac_kernel.cache import CacheStore, MemoryCache
from dotmac_kernel.flags import (
    FlagEvaluation,
    FlagOverrideRecord,
    active_flags,
    cached_evaluate,
)
from dotmac_kernel.models import Base, TimestampMixin, uuid_pk

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class FeatureFlagOverride(Base, TimestampMixin):
    """One override row: a value, a rollout percentage, or a kill switch."""

    __tablename__ = "feature_flag_overrides"
    __table_args__ = (
        Index(
            "uq_flag_overrides_platform",
            "flag_code",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
        ),
        Index(
            "uq_flag_overrides_tenant",
            "tenant_id",
            "flag_code",
            unique=True,
            postgresql_where=sa.text("tenant_id IS NOT NULL"),
        ),
        Index("ix_flag_overrides_tenant_id", "tenant_id"),
        CheckConstraint(
            "rollout_percentage IS NULL OR "
            "(rollout_percentage >= 0 AND rollout_percentage <= 100)",
            name="ck_flag_overrides_rollout_range",
        ),
        # A row that sets nothing is a row that means nothing — it would sit in
        # the precedence chain contributing no decision while looking like a
        # configured override to anyone reading the table.
        CheckConstraint(
            "value IS NOT NULL OR rollout_percentage IS NOT NULL OR kill_switch",
            name="ck_flag_overrides_not_empty",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    # NULL = deployment scope. See the module docstring.
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    flag_code: Mapped[str] = mapped_column(String(200), nullable=False)
    # JSON so one column carries every declared type; the evaluator checks the
    # value against the module's declared `value_type`.
    value: Mapped[object | None] = mapped_column(_JSON, nullable=True)
    rollout_percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kill_switch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.false()
    )
    # Free-text operator note — why this override exists. Not the machine
    # `reason` on an evaluation; this is for the human reading the table in six
    # months wondering whether the override is still needed.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    def as_record(self) -> FlagOverrideRecord:
        """The pure form the evaluator consumes."""
        return FlagOverrideRecord(
            flag_code=self.flag_code,
            tenant_id=self.tenant_id,
            value=self.value,  # type: ignore[arg-type]
            rollout_percentage=self.rollout_percentage,
            kill_switch=self.kill_switch,
            rule_id=self.id,
        )


def load_overrides(
    db: Session, *, tenant_id: UUID | None
) -> tuple[FlagOverrideRecord, ...]:
    """Every override that can apply to `tenant_id` — its own rows plus the
    deployment-scope ones. Scoped in the QUERY, not filtered afterwards: an
    evaluator that receives another tenant's rows is one refactor away from
    trusting them."""
    stmt = sa.select(FeatureFlagOverride).where(
        sa.or_(
            FeatureFlagOverride.tenant_id.is_(None),
            FeatureFlagOverride.tenant_id == tenant_id,
        )
        if tenant_id is not None
        else FeatureFlagOverride.tenant_id.is_(None)
    )
    return tuple(row.as_record() for row in db.execute(stmt).scalars())


def override_version(db: Session, *, tenant_id: UUID | None) -> int:
    """A version that changes whenever any override in scope changes.

    The most recent `updated_at` as an integer timestamp: a write to any row in
    scope moves it, which retires the whole generation of cached evaluations
    without enumerating them. Zero when there are no overrides at all, so a
    deployment that has never set one does not pay for a query result it cannot
    use.
    """
    stmt = sa.select(func.max(FeatureFlagOverride.updated_at)).where(
        sa.or_(
            FeatureFlagOverride.tenant_id.is_(None),
            FeatureFlagOverride.tenant_id == tenant_id,
        )
        if tenant_id is not None
        else FeatureFlagOverride.tenant_id.is_(None)
    )
    newest: datetime | None = db.execute(stmt).scalar()
    return 0 if newest is None else int(newest.timestamp())


# The default process-local store. A deployment that wants a shared cache
# swaps a `CacheStore` in through `install_flag_cache`; the key model comes from
# `dotmac_kernel.cache` either way, so a Redis store inherits the scope segment
# instead of inventing one.
_store: CacheStore = MemoryCache()


def install_flag_cache(store: CacheStore) -> None:
    """Swap the evaluation cache backend."""
    global _store
    _store = store


def flag_cache() -> CacheStore:
    return _store


def resolve_flag(db: Session, code: str, *, tenant_id: UUID | None) -> FlagEvaluation:
    """The DB-aware entry point a service calls to read a flag.

    Loads the overrides in scope, derives the invalidation version from them,
    and evaluates through the scoped cache. One function so a caller cannot
    accidentally evaluate against another tenant's overrides or skip the
    version — the two mistakes that turn a flag read into a data leak or a
    stale kill switch.
    """
    spec = active_flags().require(code)
    version = override_version(db, tenant_id=tenant_id)
    overrides = load_overrides(db, tenant_id=tenant_id)
    return cached_evaluate(
        spec, overrides, tenant_id=tenant_id, version=version, store=_store
    )


__all__ = [
    "FeatureFlagOverride",
    "flag_cache",
    "install_flag_cache",
    "load_overrides",
    "override_version",
    "resolve_flag",
]
