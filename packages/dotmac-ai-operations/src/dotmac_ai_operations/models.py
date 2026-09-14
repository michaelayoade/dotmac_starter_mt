"""Tenant-only AI policy, operation, attempt and advisory evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("aiops")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class AIPolicy(Base, TimestampMixin):
    __tablename__ = "ai_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_policies_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_ai_policies_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class AIPolicyVersion(Base):
    __tablename__ = "ai_policy_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_policy_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "policy_id", "version", name="uq_ai_policy_versions_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.ai_policies.tenant_id", f"{SCHEMA}.ai_policies.id"],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_operation_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_contract_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIOperation(Base, TimestampMixin):
    __tablename__ = "ai_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_operations_tenant_id_id"),
        UniqueConstraint("tenant_id", "operation_key", name="uq_ai_operations_key"),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                f"{SCHEMA}.ai_policy_versions.tenant_id",
                f"{SCHEMA}.ai_policy_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    operation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    input_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIExecutionAttempt(Base):
    __tablename__ = "ai_execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_ai_execution_attempts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "attempt_key", name="uq_ai_execution_attempts_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            [f"{SCHEMA}.ai_operations.tenant_id", f"{SCHEMA}.ai_operations.id"],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    operation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    attempt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    output_ref: Mapped[str | None] = mapped_column(String(240))
    output_digest: Mapped[str | None] = mapped_column(String(64))
    provider_observation: Mapped[str | None] = mapped_column(String(160))
    model_observation: Mapped[str | None] = mapped_column(String(160))
    request_observation: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(120))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AIInsight(Base, TimestampMixin):
    __tablename__ = "ai_insights"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_insights_tenant_id_id"),
        UniqueConstraint("tenant_id", "insight_key", name="uq_ai_insights_key"),
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            [f"{SCHEMA}.ai_operations.tenant_id", f"{SCHEMA}.ai_operations.id"],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    operation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    insight_key: Mapped[str] = mapped_column(String(200), nullable=False)
    insight_type: Mapped[str] = mapped_column(String(120), nullable=False)
    advisory_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_output_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="advisory")
    # LEGACY-DESCRIPTIVE, not renamed and kept an ordinary attribute (unlike
    # `action_evidence_ref` below, these two are NOT privatized): published
    # in `ao_0001_ai_operations` (0.1.0a1), and `service.acknowledge_insight`
    # keeps writing them EVERY call, unconditionally, for the same
    # expand-only N-1 rollback reason `action_evidence_ref` is preserved —
    # an older, rolled-back application version still reads them correctly.
    # `ao_0003_ack_attribution` gave attribution the identical
    # shadowing treatment evidence already had (Michael's ruling, round
    # 13/14): these two columns are BOTH versions' SAME columns (unlike
    # evidence, which got its own new columns), so an older writer's
    # unconditional update genuinely overwrites whatever this version wrote
    # here — see `service.acknowledge_insight`'s docstring for the exact
    # sequence. They carry no authoritative weight going forward; use
    # `service.authoritative_acknowledgement(insight).attribution` instead.
    acknowledged_by_ref: Mapped[str | None] = mapped_column(String(200))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # LEGACY, DESCRIPTIVE-ONLY, PRIVATE-BY-CONVENTION ATTRIBUTE. Column
    # `action_evidence_ref` was published in `ao_0001_ai_operations`
    # (0.1.0a1) as a naked locator with no namespace, digest or media
    # type. `ao_0002_insight_evidence_binding` (expand-only: this COLUMN
    # is preserved unrenamed, so a rolled-back application version —
    # running its OWN, separately-deployed model code, not this class —
    # still reads/writes it correctly against the expanded schema)
    # reclassifies it as descriptive metadata only. Renaming the PYTHON
    # ATTRIBUTE here (the column name passed to `mapped_column` is
    # unchanged) is deliberate and does not affect that N-1 compatibility.
    #
    # HONESTLY: this is a CONVENTION, not an enforced guard. Python does
    # not have private attributes, and nothing in this codebase adds an
    # architecture check restricting evidence reads to
    # `service.authoritative_acknowledgement(insight).evidence`. A caller
    # CAN still read `insight._legacy_action_evidence_ref` directly, go
    # through `AIInsight.__table__.c["action_evidence_ref"]`, enumerate
    # mapper attributes, serialize `__dict__`, or issue raw SQL against the
    # preserved column — none of that is prevented. The leading underscore
    # signals "do not treat this as evidence" to a reader of this class;
    # it does not and cannot stop a determined one. Use
    # `service.authoritative_acknowledgement(insight).evidence` for every
    # read; this attribute stops receiving authoritative writes from
    # `service.acknowledge_insight`, which writes the four
    # `action_evidence_*` binding columns below instead. It carries no
    # evidentiary weight and never will: a digest cannot be honestly
    # synthesised for a locator that was captured without one.
    _legacy_action_evidence_ref: Mapped[str | None] = mapped_column(
        "action_evidence_ref", String(240)
    )
    # AUTHORITATIVE typed binding (added by `ao_0002_insight_evidence_binding`,
    # additive/nullable): `AIEvidenceBinding`'s fields. `service.
    # acknowledge_insight` always writes all four together or all four
    # `None`, never a bare string — but that is a WRITER-side guarantee
    # from this one function, not a database constraint. Because the
    # legacy column above is deliberately left writable by an older,
    # rolled-back application version (that is what expand-only rollback
    # compatibility means), a race between an old writer and this one can
    # still leave a single row carrying a legacy locator alongside a full
    # (or partial) typed binding written by a different commit. This
    # module does NOT claim all-four-or-none holds at the database level,
    # and does not attempt to enforce it there.
    #
    # READ PRECEDENCE (this is what resolves that row, not prevention):
    # when the four typed columns are fully populated, they are
    # AUTHORITATIVE and the legacy locator is descriptive residue only —
    # never the reverse, and never "whichever is non-null". See
    # `service.authoritative_acknowledgement(insight).evidence`, the ONE
    # SANCTIONED function that applies this rule.
    #
    # PRIVATE-BY-CONVENTION, same treatment as the legacy locator above
    # (round 15 correction: this package previously claimed the paired
    # accessor made evidence and attribution "impossible to read apart" —
    # false, since these were ordinary public attributes; narrowing the
    # gap between that claim and the code, not only the claim, is why
    # these are private now). The PHYSICAL COLUMN NAMES are unchanged —
    # only the Python attribute names gain a leading underscore. HONESTLY,
    # stated the same way as for the legacy locator: this is still a
    # CONVENTION, not an enforced guard. A caller CAN still read
    # `insight._action_evidence_locator_ref` directly (the underscore
    # signals, it does not prevent), go through
    # `AIInsight.__table__.c["action_evidence_locator_ref"]`, enumerate
    # mapper attributes, serialize `__dict__`, or issue raw SQL — none of
    # that is closed. What IS closed: ordinary attribute access under the
    # unprefixed name no longer works at all, so a caller reaching for
    # `insight.action_evidence_locator_ref` gets `AttributeError`, not a
    # silent bypass of the paired accessor.
    _action_evidence_locator_namespace: Mapped[str | None] = mapped_column(
        "action_evidence_locator_namespace", String(120)
    )
    _action_evidence_locator_ref: Mapped[str | None] = mapped_column(
        "action_evidence_locator_ref", String(240)
    )
    _action_evidence_content_digest: Mapped[str | None] = mapped_column(
        "action_evidence_content_digest", String(64)
    )
    _action_evidence_media_type: Mapped[str | None] = mapped_column(
        "action_evidence_media_type", String(120)
    )
    # AUTHORITATIVE typed attribution (added by
    # `ao_0003_ack_attribution`, additive/nullable):
    # `AIAcknowledgementAttribution`'s fields. Gives actor/time the IDENTICAL
    # shadowing treatment evidence already got above (Michael's ruling,
    # round 13/14): a row pairing authoritative typed evidence with a
    # legacy actor/time an N-1 writer can silently overwrite makes a false
    # HISTORICAL claim, not merely a stale display one. `service.
    # acknowledge_insight` always writes all four together or all four
    # `None` — a WRITER-side guarantee, not a database constraint; there is
    # deliberately NO CHECK constraint enforcing all-four-or-none here (see
    # `ao_0003_ack_attribution`'s module docstring for why: a
    # constraint enforcing an unproven premise is worse than an honestly
    # absent one, and it is deferred to a focused successor migration once
    # real evidence exists that it holds).
    #
    # UNLIKE evidence, there is no separate legacy column pair here that
    # the older version leaves untouched: `acknowledged_by_ref`/
    # `acknowledged_at` above are the SAME columns both versions write, so
    # an N-1 writer's unconditional update genuinely overwrites them — it
    # cannot touch these four new columns at all, because it does not know
    # they exist. That is precisely why these four are never overwritten:
    # not because of a lock or a constraint, but because expand-only means
    # the older version's code has no reference to a column that did not
    # exist when it was built.
    #
    # NO BACKFILL: a row acknowledged before this migration has all four
    # of these columns `NULL` and always will — no trustworthy typed actor
    # was ever captured for it, and manufacturing one would fabricate a
    # historical claim nobody made. See `service.authoritative_acknowledgement`,
    # the ONE SANCTIONED function that resolves evidence AND attribution
    # together.
    #
    # PRIVATE-BY-CONVENTION, identical treatment and identical honest
    # limit as the evidence columns above — physical column names
    # unchanged, only the Python attribute names are prefixed, and a
    # determined reader can still reach the columns through
    # `__table__.c[...]`, mapper introspection, `__dict__`, or raw SQL.
    _attribution_actor_namespace: Mapped[str | None] = mapped_column(
        "attribution_actor_namespace", String(120)
    )
    _attribution_actor_type: Mapped[str | None] = mapped_column(
        "attribution_actor_type", String(120)
    )
    _attribution_actor_ref: Mapped[str | None] = mapped_column(
        "attribution_actor_ref", String(240)
    )
    _attribution_acknowledged_at: Mapped[datetime | None] = mapped_column(
        "attribution_acknowledged_at", DateTime(timezone=True)
    )


TENANT_MODELS = (AIPolicy, AIPolicyVersion, AIOperation, AIExecutionAttempt, AIInsight)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)
__all__ = [
    "AIExecutionAttempt",
    "AIInsight",
    "AIOperation",
    "AIPolicy",
    "AIPolicyVersion",
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
