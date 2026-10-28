"""The durable PROVISION engine: submit, claim, invoke, settle (ig_0017).

SPI 1.5 shipped the `plan`/`apply`/`observe`/`cancel` contract as a
persistence-free boundary (`provision_contract`). This module is the stateful
owner that contract always said would follow: the tables, leases, retry
floors and claim discipline that make a product-authored plan survivable
across a worker death. It mirrors the DELIVERY engine's shape deliberately —
validate before claiming, no session inside `invoke`, one conditional UPDATE
as the settle guard — because two durable engines with two claim disciplines
would be two answers to one question.

Three properties are provisioning-specific and load-bearing:

* **A raised or lost apply is never blindly re-applied.** A step whose apply
  may have landed (connector raised, lease expired mid-call, AMBIGUOUS
  result) is only ever followed by `observe`. Retrying the apply risks doing
  the effect twice and, for credential projections, a second secret fetch
  (ADR-0076 §7 allows exactly one per projection request).
* **`secret_fetch_refused` settles to `reconciliation_required`, never
  retryable.** The fetch was refused because the product-side projection
  request is closed or already consumed; retrying would re-fetch or spin.
  Recovery is a new product-side request and a new operation.
* **No secret can be submitted.** Step inputs pass the same secret-material
  refusal as configuration revisions before any row exists, so a plan can
  carry credential identifiers and versions but never the secret itself
  (ADR-0076 §7's test obligation, enforced at the write).

Operation state is a projection of step states, recomputed inside settle —
never a second writer's opinion. Steps are the claim unit; the plan phase is
claimed against the operation row itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from dotmac_kernel.models import Base, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_integration.admission import (
    AdmissionDecision,
    admit_installation,
    admit_runtime,
)
from dotmac_integration.capability_registry import CapabilityRegistry
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.dispatch import SecretResolver, _connector_error_detail
from dotmac_integration.execution import LostClaim
from dotmac_integration.lifecycle import _pinned_manifest
from dotmac_integration.models import (
    SCHEMA,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.provision_contract import (
    ProvisionHandlerRaised,
    ProvisionPlanRewritten,
    ProvisionResultInvalid,
    apply_provisioning,
    cancel_provisioning,
    observe_provisioning,
    plan_provisioning,
    provision_plan_hash,
)
from dotmac_integration.retry import retry_delay_seconds
from dotmac_integration.secret_refs import validate_no_secret_material
from dotmac_integration.spi import (
    NETWORK_TARGETS_CONFIG_KEY,
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningResult,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
    _plain_plan_value,
    accepts_manifest_digest,
    network_targets_from_json,
    network_targets_to_json,
    require_capability_mode,
)

__all__ = [
    "PROVISION_PLATFORM_TABLES",
    "SECRET_FETCH_REFUSED",
    "PreparedProvision",
    "ProvisionConflict",
    "ProvisionError",
    "ProvisionNotAdmitted",
    "ProvisionOperation",
    "ProvisionStepAttempt",
    "ProvisionSubmitRaced",
    "ProvisionUnavailable",
    "due_provision_operations",
    "invoke_provision",
    "prepare_provision",
    "request_provision_cancel",
    "settle_provision",
    "submit_provision_operation",
]

PROVISION_PLATFORM_TABLES: tuple[str, ...] = (
    "provision_operations",
    "provision_step_attempts",
)

#: The one connector error code the engine must recognise (ADR-0076 §7). It
#: is a closed engine outcome, not a branch on connector vocabulary in
#: general: it names the state where retry is forbidden by another ADR.
SECRET_FETCH_REFUSED: Final = "secret_fetch_refused"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_OPERATION_STATES: Final = (
    "planned",
    "applying",
    "succeeded",
    "failed",
    "cancelled",
    "reconciliation_required",
)
_STEP_STATES: Final = (
    "pending",
    "in_flight",
    "succeeded",
    "observing",
    "retryable",
    "terminal",
    "ambiguous",
    "cancelled",
    "reconciliation_required",
)
_NEXT_ACTIONS: Final = ("apply", "observe", "cancel")


class ProvisionError(RuntimeError):
    """A provisioning operation could not be submitted or prepared."""


class ProvisionConflict(ProvisionError):
    """The command id already names a different plan on this installation."""


class ProvisionSubmitRaced(ProvisionError):
    """A concurrent submit won; retry the same command in a fresh unit."""


class ProvisionUnavailable(ProvisionError):
    """The configuration cannot serve this operation — alert and stop."""


class ProvisionNotAdmitted(ProvisionError):
    """The runtime will not provision right now — deliberate, do not alert."""

    def __init__(self, decision: AdmissionDecision) -> None:
        super().__init__(decision.detail or decision.reason)
        self.decision = decision

    @property
    def reason(self) -> str:
        return self.decision.reason


class ProvisionOperation(Base):
    """One product-issued plan and its projected state.

    `(installation_id, command_id)` is the submission identity: resubmitting
    the same command with the same `plan_hash` replays, with a different one
    conflicts — a command id is not permission to discard a changed plan.
    Step rows are the plan's only stored form (ordered by `step_index`);
    the submitted steps are rebuilt from them, so the plan a connector
    validates is byte-identical to the one the hash names.
    """

    __tablename__ = "provision_operations"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "command_id", name="uq_provision_operations_command"
        ),
        CheckConstraint(
            "state IN ('planned', 'applying', 'succeeded', 'failed', "
            "'cancelled', 'reconciliation_required')",
            name="ck_provision_operations_state",
        ),
        CheckConstraint(
            "length(plan_hash) = 64", name="ck_provision_operations_plan_hash_len"
        ),
        CheckConstraint(
            "plan_hash ~ '^[0-9a-f]{64}$'", name="ck_provision_operations_plan_hash"
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "plan_attempt_count >= 0", name="ck_provision_operations_attempts"
        ),
        Index(
            "ix_provision_operations_state_next",
            "state",
            "next_attempt_at",
            "leased_until",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    capability_binding_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.capability_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    command_id: Mapped[str] = mapped_column(String(240), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="planned"
    )
    plan_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _plain_json(value: object) -> Any:
    """Frozen plan values back to plain JSON containers for storage.

    The SPI's own unfreezer, not a JSON round-trip: `json.dumps` cannot
    serialise the frozen `MappingProxyType` step inputs, and a `default=str`
    fallback would silently stringify nested mappings — mangling the plan
    and blinding the secret-material scan to everything nested.
    """
    return _plain_plan_value(value)


def _steps_of(db: Any, operation: ProvisionOperation) -> list[ProvisionStepAttempt]:
    from sqlalchemy import select

    return list(
        db.execute(
            select(ProvisionStepAttempt)
            .where(ProvisionStepAttempt.operation_id == operation.id)
            .order_by(ProvisionStepAttempt.step_index)
        )
        .scalars()
        .all()
    )


def _rebuild_steps(rows: Iterable[ProvisionStepAttempt]) -> tuple[ProvisionStep, ...]:
    return tuple(
        ProvisionStep(
            step_key=row.step_key,
            endpoint_code=row.endpoint_code,
            depends_on=tuple(str(item) for item in row.depends_on_json),
            input=dict(row.input_json),
        )
        for row in rows
    )


def submit_provision_operation(
    db: Any,
    *,
    capability_binding_id: UUID,
    command_id: str,
    steps: Iterable[ProvisionStep],
    registry: ConnectorRegistry,
    contracts: CapabilityRegistry | None = None,
    correlation_id: str | None = None,
) -> tuple[ProvisionOperation, bool]:
    """Persist one product-authored plan. Returns `(operation, is_new)`.

    Every refusal runs before any row exists: the binding must exist and name
    a PROVISION capability the pinned connector declares, every step input
    must satisfy the capability's command contract, and no step input may
    carry secret material (ADR-0076 §7). Resubmitting the same command with
    the same plan replays; with a different plan it conflicts.
    """
    from sqlalchemy import select

    command = command_id.strip()
    if not command:
        raise ProvisionError("command_id is required to deduplicate submission")
    if len(command) > 240:
        raise ProvisionError("command_id exceeds 240 characters")
    submitted = tuple(steps)
    if not submitted:
        raise ProvisionError("a provision plan requires at least one step")
    plan_hash = provision_plan_hash(submitted)

    binding = db.get(CapabilityBinding, capability_binding_id)
    if binding is None:
        raise ProvisionUnavailable(
            f"capability binding {capability_binding_id} does not exist"
        )
    # Rebuilding the request under the binding's own capability validates
    # step keys, dependencies and the plan hash with the SPI's rules before
    # anything is stored. The id comes from the binding, never from here:
    # the module hosts the vocabulary and must not enumerate it.
    ProvisionPlanRequest(
        capability_id=binding.capability_id,
        command_id=command,
        plan_hash=plan_hash,
        steps=submitted,
    )
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:  # pragma: no cover - FK integrity
        raise ProvisionUnavailable(f"binding {binding.id} has no installation")
    manifest = _pinned_manifest(registry, installation)
    manifest.require_declares(binding.capability_id)
    plugin = registry.plugin(installation.connector_key)
    require_capability_mode(plugin, binding.capability_id, ConnectorMode.PROVISION)

    from dotmac_integration.capability_registry import capability_registry

    contract = (contracts or capability_registry()).get(binding.capability_id)
    for step in submitted:
        plain_input = _plain_json(dict(step.input))
        # The secret-material refusal, before the row: identifiers and
        # versions may cross, secrets may not (ADR-0076 §7).
        validate_no_secret_material(plain_input, what="provision step input")
        contract.require_command(plain_input)

    def replay(existing: ProvisionOperation) -> tuple[ProvisionOperation, bool]:
        if (
            existing.plan_hash != plan_hash
            or existing.capability_binding_id != binding.id
        ):
            raise ProvisionConflict(
                "provision command id already names a different plan"
            )
        return existing, False

    existing = db.execute(
        select(ProvisionOperation).where(
            ProvisionOperation.installation_id == installation.id,
            ProvisionOperation.command_id == command,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return replay(existing)

    operation = ProvisionOperation(
        # Assigned here, not by the column default at flush: the step rows
        # below reference it in the same unit of work.
        id=uuid4(),
        installation_id=installation.id,
        capability_binding_id=binding.id,
        capability_id=binding.capability_id,
        command_id=command,
        plan_hash=plan_hash,
        state="planned",
        next_attempt_at=datetime.now(UTC),
        correlation_id=correlation_id,
    )
    step_rows = [
        ProvisionStepAttempt(
            operation_id=operation.id,
            step_index=index,
            step_key=step.step_key,
            endpoint_code=step.endpoint_code,
            depends_on_json=list(step.depends_on),
            input_json=_plain_json(dict(step.input)),
            idempotency_key=f"{command}:{step.step_key}:{plan_hash}",
            state="pending",
            next_action="apply",
            next_attempt_at=operation.next_attempt_at,
        )
        for index, step in enumerate(submitted)
    ]
    from dotmac_kernel.transactions import conflict_savepoint
    from sqlalchemy.exc import IntegrityError

    try:
        with conflict_savepoint(db):
            db.add(operation)
            db.add_all(step_rows)
            db.flush()
    except IntegrityError as exc:
        if "uq_provision_operations_command" not in str(getattr(exc, "orig", exc)):
            raise
        winner = db.execute(
            select(ProvisionOperation).where(
                ProvisionOperation.installation_id == installation.id,
                ProvisionOperation.command_id == command,
            )
        ).scalar_one_or_none()
        if winner is None:
            raise ProvisionSubmitRaced(
                "a concurrent provision submit won; retry the same command"
            ) from None
        return replay(winner)
    return operation, True


class ProvisionStepAttempt(Base):
    """One plan step and its claim/retry state — the claim unit.

    `next_action` says which handler operation the next claim invokes. It is
    engine state, set at settle, and it is what guarantees an apply whose
    outcome is unknown is followed by `observe` and never by a blind
    re-apply: the transition happens in the settle write, not in a worker's
    memory.
    """

    __tablename__ = "provision_step_attempts"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "step_key", name="uq_provision_steps_operation_key"
        ),
        CheckConstraint(
            "state IN ('pending', 'in_flight', 'succeeded', 'observing', "
            "'retryable', 'terminal', 'ambiguous', 'cancelled', "
            "'reconciliation_required')",
            name="ck_provision_steps_state",
        ),
        CheckConstraint(
            "next_action IN ('apply', 'observe', 'cancel')",
            name="ck_provision_steps_next_action",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_provision_steps_attempts"),
        CheckConstraint("step_index >= 0", name="ck_provision_steps_index"),
        Index(
            "ix_provision_steps_due",
            "operation_id",
            "state",
            "next_attempt_at",
            "leased_until",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provision_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_key: Mapped[str] = mapped_column(String(160), nullable=False)
    endpoint_code: Mapped[str] = mapped_column(String(160), nullable=False)
    depends_on_json: Mapped[list[object]] = mapped_column(_JSON, nullable=False)
    input_json: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    next_action: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="apply"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_operation_ref: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    evidence_json: Mapped[dict[str, object] | None] = mapped_column(
        _JSON, nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


@dataclass(frozen=True, slots=True)
class PreparedProvision:
    """Everything invoke needs, and nothing that holds a connection."""

    operation_id: UUID
    step_id: UUID | None
    kind: str  # "plan" | "apply" | "observe" | "cancel"
    installation_id: UUID
    binding_id: UUID
    connector_key: str
    capability_id: str
    command_id: str
    plan_hash: str
    steps: tuple[ProvisionStep, ...]
    step: ProvisionStep | None
    step_key: str | None
    provider_operation_ref: str | None
    cancel_reason: str
    config: dict[str, object]
    secret_refs: dict[str, str]
    idempotency_key: str
    config_revision_id: UUID | None
    attempt_number: int

    @property
    def operation_ref(self) -> str:
        return str(self.operation_id)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _claimable(step: ProvisionStepAttempt, moment: datetime) -> bool:
    if step.state not in (
        "pending",
        "retryable",
        "observing",
        "ambiguous",
        "in_flight",
    ):
        return False
    if step.leased_until is not None and _aware(step.leased_until) >= moment:
        return False
    return step.next_attempt_at is None or _aware(step.next_attempt_at) <= moment


def due_provision_operations(
    db: Any, *, limit: int = 100, now: datetime | None = None
) -> tuple[UUID, ...]:
    """Operation ids with claimable work, earliest floor first. Claims nothing.

    The sole selector, bounded like `due_polling_jobs`: a worker wakes on its
    own cadence and asks what is eligible; the claim itself is
    `prepare_provision`'s conditional UPDATE.
    """
    from sqlalchemy import or_, select

    if not 1 <= limit <= 1000:
        raise ProvisionError("due selection limit must be between 1 and 1000")
    moment = now or datetime.now(UTC)
    due: dict[UUID, datetime] = {}

    plan_rows = db.execute(
        select(ProvisionOperation).where(
            ProvisionOperation.state == "planned",
            ProvisionOperation.plan_validated_at.is_(None),
            or_(
                ProvisionOperation.leased_until.is_(None),
                ProvisionOperation.leased_until < moment,
            ),
            or_(
                ProvisionOperation.next_attempt_at.is_(None),
                ProvisionOperation.next_attempt_at <= moment,
            ),
        )
    ).scalars()
    for operation in plan_rows:
        due[operation.id] = _aware(operation.next_attempt_at or operation.created_at)

    step_rows = (
        db.execute(
            select(ProvisionStepAttempt)
            .where(
                ProvisionStepAttempt.state.in_(
                    ("pending", "retryable", "observing", "ambiguous", "in_flight")
                ),
                or_(
                    ProvisionStepAttempt.leased_until.is_(None),
                    ProvisionStepAttempt.leased_until < moment,
                ),
                or_(
                    ProvisionStepAttempt.next_attempt_at.is_(None),
                    ProvisionStepAttempt.next_attempt_at <= moment,
                ),
            )
            .order_by(ProvisionStepAttempt.next_attempt_at)
            .limit(1000)
        )
        .scalars()
        .all()
    )
    for step in step_rows:
        floor = _aware(step.next_attempt_at or step.created_at)
        if step.operation_id not in due or floor < due[step.operation_id]:
            due[step.operation_id] = floor
    ordered = sorted(due.items(), key=lambda item: (item[1], str(item[0])))
    return tuple(operation_id for operation_id, _ in ordered[:limit])


def prepare_provision(
    db: Any,
    operation: ProvisionOperation,
    *,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> PreparedProvision | None:
    """Validate first, THEN claim one unit of work. `None` = claim lost.

    Mirrors `dispatch.prepare`: every refusal runs against unclaimed state,
    and the conditional UPDATE that takes the lease is the last thing that
    happens. A unit is the plan validation (claimed on the operation row)
    or one step's next action.
    """
    from sqlalchemy import or_, update

    halted = admit_runtime(policy)
    if not halted.admitted:
        raise ProvisionNotAdmitted(halted)
    if operation.state in (
        "succeeded",
        "failed",
        "cancelled",
        "reconciliation_required",
    ):
        return None

    binding = db.get(CapabilityBinding, operation.capability_binding_id)
    if binding is None:
        raise ProvisionUnavailable(
            f"operation {operation.id} names no capability binding"
        )
    if binding.state != "enabled":
        raise ProvisionUnavailable(
            f"binding {binding.id} is {binding.state!r}, not enabled"
        )
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:
        raise ProvisionUnavailable(f"binding {binding.id} has no installation")
    admitted = admit_installation(db, installation, policy=policy, now=now)
    if not admitted.admitted:
        raise ProvisionNotAdmitted(admitted)
    if installation.state != "enabled":
        raise ProvisionUnavailable(
            f"installation {installation.name!r} is {installation.state!r}, "
            "not enabled"
        )
    try:
        registry.require_compatible(installation.connector_key)
        plugin = registry.plugin(installation.connector_key)
    except Exception as exc:
        raise ProvisionUnavailable(
            f"connector {installation.connector_key!r} is not usable in this "
            f"runtime: {exc}"
        ) from exc
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise ProvisionUnavailable(
            f"installation {installation.name!r} is pinned to manifest "
            f"{installation.manifest_digest[:12]}, which connector "
            f"{installation.connector_key!r} no longer honours"
        )

    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id
        else None
    )
    config = dict((revision.config_json if revision else {}) or {})
    # Engine-owned, overwritten unconditionally: the connector is handed the
    # targets this revision bound, never a same-named config entry.
    config[NETWORK_TARGETS_CONFIG_KEY] = network_targets_to_json(
        network_targets_from_json(revision.network_targets_json if revision else None)
    )
    secret_refs = dict((revision.secret_refs if revision else {}) or {})
    moment = now or datetime.now(UTC)
    lease_until = moment + timedelta(seconds=policy.lease_seconds)
    rows = _steps_of(db, operation)
    steps = _rebuild_steps(rows)
    if provision_plan_hash(steps) != operation.plan_hash:  # pragma: no cover
        raise ProvisionUnavailable(
            f"operation {operation.id} stored steps no longer hash to its plan"
        )

    base = {
        "operation_id": operation.id,
        "installation_id": installation.id,
        "binding_id": binding.id,
        "connector_key": installation.connector_key,
        "capability_id": binding.capability_id,
        "command_id": operation.command_id,
        "plan_hash": operation.plan_hash,
        "steps": steps,
        "cancel_reason": operation.cancel_reason or "",
        "config": config,
        "secret_refs": secret_refs,
        "config_revision_id": revision.id if revision else None,
    }

    # Cancel bookkeeping first: steps that never reached a provider need no
    # invoke to become cancelled.
    if operation.cancel_requested_at is not None:
        for row in rows:
            if row.state in ("pending", "retryable") and (
                row.provider_operation_ref is None and row.next_action == "apply"
            ):
                row.state = "cancelled"
                row.leased_until = None
                row.next_attempt_at = None
                row.finished_at = moment
        db.flush()

    if operation.plan_validated_at is None:
        if operation.cancel_requested_at is not None:
            _project_operation_state(db, operation, moment)
            return None
        result = db.execute(
            update(ProvisionOperation)
            .where(
                ProvisionOperation.id == operation.id,
                ProvisionOperation.plan_validated_at.is_(None),
                ProvisionOperation.state == "planned",
                or_(
                    ProvisionOperation.leased_until.is_(None),
                    ProvisionOperation.leased_until < moment,
                ),
                or_(
                    ProvisionOperation.next_attempt_at.is_(None),
                    ProvisionOperation.next_attempt_at <= moment,
                ),
            )
            .values(
                leased_until=lease_until,
                plan_attempt_count=ProvisionOperation.plan_attempt_count + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        db.refresh(operation)
        return PreparedProvision(
            **base,
            step_id=None,
            kind="plan",
            step=None,
            step_key=None,
            provider_operation_ref=None,
            idempotency_key=f"{operation.command_id}:plan:{operation.plan_hash}",
            attempt_number=operation.plan_attempt_count,
        )

    succeeded = {row.step_key for row in rows if row.state == "succeeded"}
    candidates = [row for row in rows if _claimable(row, moment)]
    if operation.cancel_requested_at is not None:
        candidates = [
            row
            for row in candidates
            if row.next_action == "cancel" or row.provider_operation_ref is not None
        ]
        candidates.sort(key=lambda row: -row.step_index)
    else:
        candidates = [
            row
            for row in candidates
            if row.next_action != "apply"
            or {str(item) for item in row.depends_on_json} <= succeeded
        ]
        candidates.sort(key=lambda row: row.step_index)
    for row in candidates:
        kind = row.next_action
        new_action = kind
        if row.state == "in_flight" and kind == "apply":
            # The previous claim's outcome is unknown: observe, never re-apply.
            kind = "observe"
            new_action = "observe"
        elif operation.cancel_requested_at is not None and kind != "cancel":
            kind = "cancel"
            new_action = "cancel"
        result = db.execute(
            update(ProvisionStepAttempt)
            .where(
                ProvisionStepAttempt.id == row.id,
                ProvisionStepAttempt.state.in_(
                    ("pending", "retryable", "observing", "ambiguous", "in_flight")
                ),
                ProvisionStepAttempt.attempt_count == row.attempt_count,
                or_(
                    ProvisionStepAttempt.leased_until.is_(None),
                    ProvisionStepAttempt.leased_until < moment,
                ),
                or_(
                    ProvisionStepAttempt.next_attempt_at.is_(None),
                    ProvisionStepAttempt.next_attempt_at <= moment,
                ),
            )
            .values(
                state="in_flight",
                next_action=new_action,
                attempt_count=ProvisionStepAttempt.attempt_count + 1,
                leased_until=lease_until,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        db.refresh(row)
        step = next(item for item in steps if item.step_key == row.step_key)
        return PreparedProvision(
            **base,
            step_id=row.id,
            kind=kind,
            step=step,
            step_key=row.step_key,
            provider_operation_ref=row.provider_operation_ref,
            idempotency_key=row.idempotency_key,
            attempt_number=row.attempt_count,
        )
    # Nothing claimable: still an observation about the operation. The cancel
    # bookkeeping above (or a dependency that can never be met) may have made
    # it terminal, and leaving that unprojected would strand it in `applying`.
    _project_operation_state(db, operation, moment)
    return None


def invoke_provision(
    prepared: PreparedProvision,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> ProvisioningResult:
    """Call the connector. NO session, NO transaction, by signature.

    Boundary failures are mapped by kind: `plan` and `observe` cannot have
    landed an effect, so a raise is retryable; `apply` and `cancel` may
    have, so a raise is AMBIGUOUS (observe next, never blind-reapply). A
    rewritten plan is terminal by definition.
    """
    plugin = registry.plugin(prepared.connector_key)
    require_capability_mode(plugin, prepared.capability_id, ConnectorMode.PROVISION)
    secrets = dict(resolve_secrets(prepared.secret_refs))
    config: Mapping[str, object] = dict(prepared.config)

    def _mapped(
        code: str, detail_exc: BaseException | None = None
    ) -> ProvisioningResult:
        if code == "plan_rewritten":
            return ProvisioningResult(
                status=ProvisionResultStatus.TERMINAL, error_code="plan_rewritten"
            )
        retryable_kinds = ("plan", "observe")
        status = (
            ProvisionResultStatus.RETRYABLE
            if prepared.kind in retryable_kinds
            else ProvisionResultStatus.AMBIGUOUS
        )
        evidence: dict[str, object] = {}
        if detail_exc is not None:
            evidence["exception_type"] = _connector_error_detail(detail_exc)
        return ProvisioningResult(status=status, error_code=code, evidence=evidence)

    try:
        if prepared.kind == "plan":
            plan_result = plan_provisioning(
                plugin,
                ProvisionPlanRequest(
                    capability_id=prepared.capability_id,
                    command_id=prepared.command_id,
                    plan_hash=prepared.plan_hash,
                    steps=prepared.steps,
                    config=config,
                    secrets=secrets,
                ),
            )
            return ProvisioningResult(
                status=ProvisionResultStatus.SUCCEEDED,
                evidence=dict(plan_result.evidence),
            )
        step = prepared.step
        if step is None:  # non-plan kinds always carry a step
            raise RuntimeError("prepared non-plan unit without a step")
        if prepared.kind == "apply":
            return apply_provisioning(
                plugin,
                ProvisionApplyRequest(
                    capability_id=prepared.capability_id,
                    command_id=prepared.command_id,
                    operation_ref=prepared.operation_ref,
                    plan_hash=prepared.plan_hash,
                    step=step,
                    config=config,
                    secrets=secrets,
                    idempotency_key=prepared.idempotency_key,
                ),
            )
        if prepared.kind == "observe":
            return observe_provisioning(
                plugin,
                ProvisionObserveRequest(
                    capability_id=prepared.capability_id,
                    command_id=prepared.command_id,
                    operation_ref=prepared.operation_ref,
                    plan_hash=prepared.plan_hash,
                    step_key=step.step_key,
                    provider_operation_ref=prepared.provider_operation_ref or "",
                    target=dict(step.input),
                    config=config,
                    secrets=secrets,
                ),
            )
        return cancel_provisioning(
            plugin,
            ProvisionCancelRequest(
                capability_id=prepared.capability_id,
                command_id=prepared.command_id,
                operation_ref=prepared.operation_ref,
                plan_hash=prepared.plan_hash,
                step_key=step.step_key,
                provider_operation_ref=prepared.provider_operation_ref or "",
                target=dict(step.input),
                reason=prepared.cancel_reason or "cancelled by owning product",
                idempotency_key=prepared.idempotency_key + ":cancel",
                config=config,
                secrets=secrets,
            ),
        )
    except ProvisionPlanRewritten:
        return _mapped("plan_rewritten")
    except ProvisionResultInvalid:
        return _mapped("connector_contract")
    except ProvisionHandlerRaised as exc:
        return _mapped("connector_raised", exc)


def _step_settlement(kind: str, result: ProvisioningResult) -> tuple[str, str, bool]:
    """Map a result onto (state, next_action, finished) for one step."""
    if result.error_code == SECRET_FETCH_REFUSED and (
        result.status is not ProvisionResultStatus.SUCCEEDED
    ):
        return "reconciliation_required", "observe", True
    status = result.status
    if kind == "apply":
        return {
            ProvisionResultStatus.SUCCEEDED: ("succeeded", "apply", True),
            ProvisionResultStatus.ACCEPTED: ("observing", "observe", False),
            ProvisionResultStatus.PENDING: ("observing", "observe", False),
            ProvisionResultStatus.RETRYABLE: ("retryable", "apply", False),
            ProvisionResultStatus.TERMINAL: ("terminal", "apply", True),
            ProvisionResultStatus.AMBIGUOUS: ("ambiguous", "observe", False),
            ProvisionResultStatus.CANCELLED: ("cancelled", "cancel", True),
            ProvisionResultStatus.NOT_FOUND: ("terminal", "apply", True),
        }[status]
    if kind == "observe":
        return {
            ProvisionResultStatus.SUCCEEDED: ("succeeded", "observe", True),
            ProvisionResultStatus.ACCEPTED: ("observing", "observe", False),
            ProvisionResultStatus.PENDING: ("observing", "observe", False),
            ProvisionResultStatus.RETRYABLE: ("observing", "observe", False),
            ProvisionResultStatus.TERMINAL: ("terminal", "observe", True),
            ProvisionResultStatus.AMBIGUOUS: ("ambiguous", "observe", False),
            ProvisionResultStatus.CANCELLED: ("cancelled", "cancel", True),
            ProvisionResultStatus.NOT_FOUND: (
                "reconciliation_required",
                "observe",
                True,
            ),
        }[status]
    # cancel
    return {
        ProvisionResultStatus.SUCCEEDED: ("cancelled", "cancel", True),
        ProvisionResultStatus.CANCELLED: ("cancelled", "cancel", True),
        ProvisionResultStatus.ACCEPTED: ("observing", "observe", False),
        ProvisionResultStatus.PENDING: ("observing", "observe", False),
        ProvisionResultStatus.RETRYABLE: ("retryable", "cancel", False),
        ProvisionResultStatus.TERMINAL: ("terminal", "cancel", True),
        ProvisionResultStatus.AMBIGUOUS: ("ambiguous", "observe", False),
        # A NOT_FOUND cancel proves neither absence nor prior removal (a18).
        ProvisionResultStatus.NOT_FOUND: ("reconciliation_required", "cancel", True),
    }[status]


def _project_operation_state(
    db: Any, operation: ProvisionOperation, moment: datetime
) -> None:
    """Recompute the operation state from its steps, in settle's transaction."""
    if operation.state in (
        "succeeded",
        "failed",
        "cancelled",
        "reconciliation_required",
    ):
        return
    rows = _steps_of(db, operation)
    states = [row.state for row in rows]
    if rows and all(state == "succeeded" for state in states):
        operation.state = "succeeded"
        operation.finished_at = moment
        operation.leased_until = None
        operation.next_attempt_at = None
        db.flush()
        return
    active = ("in_flight", "retryable", "observing", "ambiguous")
    if any(state in active for state in states):
        if operation.plan_validated_at is not None:
            operation.state = "applying"
        db.flush()
        return
    by_key = {row.step_key: row for row in rows}
    progressable = {
        row.step_key
        for row in rows
        if row.state == "pending"
        and all(by_key[str(dep)].state == "succeeded" for dep in row.depends_on_json)
    }
    if progressable or operation.plan_validated_at is None:
        if operation.plan_validated_at is not None:
            operation.state = "applying"
        db.flush()
        return
    # Nothing can move: only finished steps and dependency-blocked pending.
    if "reconciliation_required" in states:
        operation.state = "reconciliation_required"
    elif operation.cancel_requested_at is not None and "cancelled" in states:
        operation.state = "cancelled"
    elif "terminal" in states or "pending" in states:
        operation.state = "failed"
    elif states and all(state == "cancelled" for state in states):
        operation.state = "cancelled"
    else:  # pragma: no cover - defensive
        operation.state = "failed"
    operation.finished_at = moment
    operation.leased_until = None
    operation.next_attempt_at = None
    db.flush()


def settle_provision(
    db: Any,
    *,
    prepared: PreparedProvision,
    result: ProvisioningResult,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> ProvisionOperation:
    """Record one outcome in ONE conditional UPDATE guarded by the claim.

    Same discipline as `dispatch.settle`: state, attempt count and lease are
    all in the WHERE clause, and `rowcount != 1` is `LostClaim`. Retryable
    work rides the shared saturating curve with no attempt cap (the POLL
    rule): a provisioning job is never dead-lettered silently — terminal,
    ambiguous-parked and reconciliation states are operator-visible, and
    cancel is a product's lifecycle gesture.
    """
    from sqlalchemy import update

    moment = now or datetime.now(UTC)
    operation: ProvisionOperation | None = db.get(
        ProvisionOperation, prepared.operation_id
    )
    if operation is None:  # pragma: no cover - FK integrity
        raise ProvisionUnavailable(f"operation {prepared.operation_id} is gone")

    if prepared.kind == "plan":
        if result.status is ProvisionResultStatus.SUCCEEDED:
            values: dict[str, Any] = {
                "plan_validated_at": moment,
                "state": "applying",
                "leased_until": None,
                "next_attempt_at": moment,
                "error_code": None,
            }
        elif result.status is ProvisionResultStatus.TERMINAL:
            values = {
                "state": "failed",
                "leased_until": None,
                "next_attempt_at": None,
                "error_code": result.error_code,
                "finished_at": moment,
            }
        else:
            values = {
                "leased_until": None,
                "next_attempt_at": moment
                + timedelta(
                    seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
                ),
                "error_code": result.error_code,
            }
        settled = db.execute(
            update(ProvisionOperation)
            .where(
                ProvisionOperation.id == operation.id,
                ProvisionOperation.plan_validated_at.is_(None),
                ProvisionOperation.plan_attempt_count == prepared.attempt_number,
                ProvisionOperation.leased_until.is_not(None),
                ProvisionOperation.leased_until >= moment,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if settled.rowcount != 1:
            raise LostClaim(
                f"operation {operation.id}: this worker no longer holds the "
                f"plan claim it took at attempt {prepared.attempt_number}"
            )
        db.refresh(operation)
        return operation

    if prepared.step_id is None:
        raise RuntimeError("prepared non-plan unit without a step id")
    state, next_action, finished = _step_settlement(prepared.kind, result)
    values = {
        "state": state,
        "next_action": next_action,
        "leased_until": None,
        "error_code": None if state == "succeeded" else result.error_code,
        "evidence_json": _plain_json(dict(result.evidence)) or None,
        "finished_at": moment if finished else None,
        "next_attempt_at": (
            None
            if finished
            else moment
            + timedelta(
                seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
            )
        ),
    }
    if result.provider_operation_ref is not None:
        values["provider_operation_ref"] = result.provider_operation_ref
    settled = db.execute(
        update(ProvisionStepAttempt)
        .where(
            ProvisionStepAttempt.id == prepared.step_id,
            ProvisionStepAttempt.state == "in_flight",
            ProvisionStepAttempt.attempt_count == prepared.attempt_number,
            ProvisionStepAttempt.leased_until.is_not(None),
            ProvisionStepAttempt.leased_until >= moment,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if settled.rowcount != 1:
        raise LostClaim(
            f"step {prepared.step_key!r} of operation {operation.id}: this "
            f"worker no longer holds the claim it took at attempt "
            f"{prepared.attempt_number}"
        )
    if result.error_code is not None and operation.error_code is None:
        operation.error_code = result.error_code
    _project_operation_state(db, operation, moment)
    db.refresh(operation)
    return operation


def request_provision_cancel(
    db: Any,
    operation: ProvisionOperation,
    *,
    reason: str = "",
    now: datetime | None = None,
) -> ProvisionOperation:
    """Record the owning product's cancel decision. Idempotent.

    Only records the decision: steps that never reached a provider are
    cancelled by `prepare_provision`'s bookkeeping, steps with a provider
    operation are cancelled through the connector under their own claims.
    """
    if operation.state in (
        "succeeded",
        "failed",
        "cancelled",
        "reconciliation_required",
    ):
        return operation
    if operation.cancel_requested_at is None:
        operation.cancel_requested_at = now or datetime.now(UTC)
        operation.cancel_reason = reason.strip()[:240] or None
        operation.next_attempt_at = operation.cancel_requested_at
        db.flush()
    return operation
