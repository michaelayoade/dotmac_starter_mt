"""Flush-only provider-neutral AI policy and evidence owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from dotmac_ai_operations.advisory import (
    AIAcknowledgementAttribution,
    AIEvidenceBinding,
    AIExecutionObservation,
)
from dotmac_ai_operations.contracts import AIOperationIntent, InsightInput
from dotmac_ai_operations.models import (
    AIExecutionAttempt,
    AIInsight,
    AIOperation,
    AIPolicy,
    AIPolicyVersion,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


class AIOperationRefused(ValueError):
    """An AI operation cannot preserve the published policy/evidence contract."""


def _aware(value: datetime, name: str) -> None:
    # Checked before calling any `datetime` method: a non-`datetime`
    # `value` (``None``, a string, ...) must raise the same `ValueError`
    # every other refusal in this module raises, not an uncaught
    # `AttributeError` from calling `.utcoffset()` on something that
    # doesn't have it.
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime instance, got {type(value)!r}")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonicalize_utc(value: datetime) -> datetime:
    """Canonicalize an aware ``datetime`` to a PLAIN ``datetime`` fixed to
    the ``timezone.utc`` singleton — reconstructed via field accessors,
    never trusted as the ``astimezone()`` result directly.

    Round 14 finding: ``record_attempt`` used to canonicalize once for the
    idempotency digest and then persist the ORIGINAL, still
    caller-controlled ``observed_at`` into both
    ``AIExecutionAttempt.observed_at`` and ``operation.completed_at``. A
    stateful ``tzinfo`` can return one offset during validation and a
    different one — or raise — when the driver serializes it later, at
    ``db.flush()``, which is outside this function's own ``try``. Calling
    this ONCE and using its single return value for the digest AND every
    persisted timestamp removes the caller's ``tzinfo`` object from every
    path after this call: nothing downstream ever touches it again, so it
    cannot disagree with itself or raise a second time.

    Also the representation-independent fingerprint property this always
    had: two values naming the same instant under different offsets
    (e.g. ``2026-09-14T10:00:00+01:00`` and ``2026-09-14T09:00:00+00:00``,
    which ``timestamptz`` stores identically) canonicalize identically —
    hashing/storing the original value directly does not have this
    property, since it depends on the caller's chosen offset, not the
    instant.
    """
    converted = value.astimezone(UTC)
    return datetime(
        converted.year,
        converted.month,
        converted.day,
        converted.hour,
        converted.minute,
        converted.second,
        converted.microsecond,
        tzinfo=UTC,
    )


def create_policy(db: Session, *, tenant_id: UUID, code: str, title: str) -> AIPolicy:
    if not code.strip() or not title.strip():
        raise AIOperationRefused("policy code and title are required")
    row = AIPolicy(tenant_id=tenant_id, code=code, title=title, active=True)
    db.add(row)
    db.flush()
    return row


def publish_policy_version(
    db: Session,
    *,
    tenant_id: UUID,
    policy_id: UUID,
    allowed_operation_kinds: tuple[str, ...],
    input_contract_ref: str,
    published_at: datetime,
) -> AIPolicyVersion:
    _aware(published_at, "published_at")
    policy = db.scalar(
        select(AIPolicy).where(
            AIPolicy.tenant_id == tenant_id,
            AIPolicy.id == policy_id,
            AIPolicy.active.is_(True),
        )
    )
    kinds = sorted(set(allowed_operation_kinds))
    if policy is None or not kinds or not input_contract_ref.strip():
        raise AIOperationRefused(
            "active policy, operation kinds and input contract are required"
        )
    version = (
        int(
            db.scalar(
                select(func.max(AIPolicyVersion.version)).where(
                    AIPolicyVersion.tenant_id == tenant_id,
                    AIPolicyVersion.policy_id == policy_id,
                )
            )
            or 0
        )
        + 1
    )
    digest = _digest(
        {
            "policy_code": policy.code,
            "version": version,
            "operation_kinds": kinds,
            "input_contract_ref": input_contract_ref,
        }
    )
    row = AIPolicyVersion(
        tenant_id=tenant_id,
        policy_id=policy_id,
        version=version,
        allowed_operation_kinds=kinds,
        input_contract_ref=input_contract_ref,
        policy_digest=digest,
        active=False,
        published_at=published_at,
    )
    db.add(row)
    db.flush()
    return row


def activate_policy_version(
    db: Session, *, tenant_id: UUID, version_id: UUID, activated_at: datetime
) -> AIPolicyVersion:
    _aware(activated_at, "activated_at")
    version = db.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id, AIPolicyVersion.id == version_id
        )
    )
    if version is None:
        raise AIOperationRefused("policy version not found")
    for row in db.scalars(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id,
            AIPolicyVersion.policy_id == version.policy_id,
            AIPolicyVersion.active.is_(True),
        )
    ).all():
        row.active = False
    version.active = True
    version.activated_at = activated_at
    db.flush()
    return version


def start_operation(
    db: Session,
    *,
    tenant_id: UUID,
    operation_key: str,
    policy_version_id: UUID,
    operation_kind: str,
    input_ref: str,
    input_digest: str,
    started_at: datetime,
) -> tuple[AIOperation, AIOperationIntent]:
    _aware(started_at, "started_at")
    version = db.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id,
            AIPolicyVersion.id == policy_version_id,
            AIPolicyVersion.active.is_(True),
        )
    )
    if version is None:
        raise AIOperationRefused("active policy version not found")
    if operation_kind not in version.allowed_operation_kinds:
        raise AIOperationRefused("operation kind is not allowed by the active policy")
    fingerprint = _digest(
        {
            "policy_version_id": str(policy_version_id),
            "operation_kind": operation_kind,
            "input_ref": input_ref,
            "input_digest": input_digest,
        }
    )
    existing = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id,
            AIOperation.operation_key == operation_key,
        )
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise AIOperationRefused("operation key reused with different content")
        return existing, _intent(existing, version.policy_digest)
    operation = AIOperation(
        tenant_id=tenant_id,
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        policy_version_id=version.id,
        operation_kind=operation_kind,
        input_ref=input_ref,
        input_digest=input_digest,
        status="pending",
        started_at=started_at,
    )
    db.add(operation)
    db.flush()
    return operation, _intent(operation, version.policy_digest)


def _intent(operation: AIOperation, policy_digest: str) -> AIOperationIntent:
    return AIOperationIntent(
        f"ai-operation:{operation.id}",
        operation.id,
        f"ai.{operation.operation_kind}.execute",
        operation.input_ref,
        operation.input_digest,
        policy_digest,
    )


def record_attempt(
    db: Session,
    *,
    tenant_id: UUID,
    operation_id: UUID,
    attempt_key: str,
    outcome: str,
    output_ref: str | None,
    output_digest: str | None,
    provider_observation: str | None,
    model_observation: str | None,
    request_observation: str | None,
    error_code: str | None,
    observed_at: datetime,
) -> AIExecutionAttempt:
    """Record an execution attempt.

    This function constructs the canonical ``AIExecutionObservation``
    itself, from raw inputs — it never accepts a pre-built object from the
    caller. That is deliberate: a caller cannot bypass
    ``AIExecutionObservation``'s invariants (paired output evidence,
    failure-evidence requirement, digest shape, and so on) by handing this
    function an unvalidated ``AttemptInput`` instead, because there is no
    parameter here a pre-built object could be passed through. Every
    invariant is enforced before anything reaches durable storage or the
    idempotency fingerprint below.

    A malformed input (bad outcome, unpaired output evidence, a malformed
    digest, an ``observed_at`` that is not a ``datetime``, a naive
    ``observed_at``, an aware ``observed_at`` so far from UTC midnight
    that canonicalizing it overflows, an ``observed_at`` whose ``tzinfo``
    itself raises on ``utcoffset()``/``astimezone()``, ...) is reported as
    ``AIOperationRefused``. Precisely, and this time made TRUE rather than
    narrowed again: ``_aware``, UTC canonicalization, and
    ``AIExecutionObservation``'s construction all run inside one ``try``
    that catches ``Exception`` broadly — not a named subset — and
    re-raises as ``AIOperationRefused``. This claim has already been
    found incomplete THREE times naming a narrower exception set each
    time (canonicalization originally ran outside the ``try``; ``_aware``
    originally called a ``datetime`` method without checking the value
    was a ``datetime``; ``(ValueError, OverflowError)`` still let a
    caller-supplied ``tzinfo`` whose ``utcoffset()`` raises, e.g.
    ``RuntimeError``, leak past this function raw). Catching broadly here
    is a deliberate trade-off, not an oversight: it also swallows a
    genuine bug inside this three-line block, re-reporting it as a
    refusal instead of surfacing it as an unhandled exception. That is
    accepted because ``observed_at`` and its ``tzinfo`` are ALWAYS
    caller-supplied, arbitrary, untrusted input to this function, and the
    documented contract is that malformed caller input is refused, not
    that this block's own three lines are bug-free — the narrower
    catches never held that boundary and kept failing on the next
    caller-controlled edge, not on a bug inside this function.
    ``observed_at`` is canonicalized to UTC EXACTLY ONCE (round 14 finding:
    it previously canonicalized only for the idempotency digest and then
    persisted the ORIGINAL, still caller-controlled value into both
    ``AIExecutionAttempt.observed_at`` and ``operation.completed_at`` — a
    stateful ``tzinfo`` could disagree with itself, or raise outside this
    function's own ``try``, when the driver serialized it later at
    ``db.flush()``). The single canonical value is used for the
    idempotency fingerprint AND both persisted timestamps: two values
    naming the same instant under different offsets fingerprint AND
    persist identically, since a ``timestamptz`` column stores them
    identically.
    """
    try:
        _aware(observed_at, "observed_at")
        # Canonicalize EXACTLY ONCE. `canonical_observed_at` — a plain
        # datetime fixed to `timezone.utc`, not the original — is the
        # single value used for the digest below and both persisted
        # timestamps further down; nothing after this line reads
        # `observed_at` (the original, still caller-controlled parameter)
        # again. See `_canonicalize_utc`'s docstring for why.
        canonical_observed_at = _canonicalize_utc(observed_at)
        observation = AIExecutionObservation(
            attempt_key=attempt_key,
            outcome=outcome,
            output_ref=output_ref,
            output_digest=output_digest,
            provider_observation=provider_observation,
            model_observation=model_observation,
            request_observation=request_observation,
            error_code=error_code,
        )
    except Exception as exc:  # see the docstring above: this
        # boundary's contract is "malformed caller input is refused", and
        # `observed_at`'s `tzinfo` is caller-supplied, arbitrary code this
        # module does not control (it can raise anything from
        # `utcoffset()`/`astimezone()`); (ValueError, OverflowError) was
        # narrowed here three times already and a new caller-controlled
        # exception type kept finding the gap each time.
        raise AIOperationRefused(str(exc)) from exc
    digest = _digest(
        [
            str(operation_id),
            observation.attempt_key,
            observation.outcome,
            observation.output_ref,
            observation.output_digest,
            observation.provider_observation,
            observation.model_observation,
            observation.request_observation,
            observation.error_code,
            canonical_observed_at.isoformat(),
        ]
    )
    existing = db.scalar(
        select(AIExecutionAttempt).where(
            AIExecutionAttempt.tenant_id == tenant_id,
            AIExecutionAttempt.attempt_key == observation.attempt_key,
        )
    )
    if existing:
        if existing.observation_digest != digest:
            raise AIOperationRefused("attempt key reused with different observation")
        return existing
    operation = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id, AIOperation.id == operation_id
        )
    )
    if operation is None or operation.status not in {"pending", "failed"}:
        raise AIOperationRefused("operation is not awaiting an attempt")
    row = AIExecutionAttempt(
        tenant_id=tenant_id,
        operation_id=operation.id,
        attempt_key=observation.attempt_key,
        observation_digest=digest,
        outcome=observation.outcome,
        output_ref=observation.output_ref,
        output_digest=observation.output_digest,
        provider_observation=observation.provider_observation,
        model_observation=observation.model_observation,
        request_observation=observation.request_observation,
        error_code=observation.error_code,
        observed_at=canonical_observed_at,
    )
    operation.status = observation.outcome
    operation.completed_at = (
        canonical_observed_at if observation.outcome == "succeeded" else None
    )
    db.add(row)
    db.flush()
    return row


def create_insight(
    db: Session,
    *,
    tenant_id: UUID,
    operation_id: UUID,
    command: InsightInput,
    created_at: datetime,
) -> AIInsight:
    _aware(created_at, "created_at")
    operation = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id,
            AIOperation.id == operation_id,
            AIOperation.status == "succeeded",
        )
    )
    matching = db.scalar(
        select(AIExecutionAttempt.id).where(
            AIExecutionAttempt.tenant_id == tenant_id,
            AIExecutionAttempt.operation_id == operation_id,
            AIExecutionAttempt.output_digest == command.source_output_digest,
            AIExecutionAttempt.outcome == "succeeded",
        )
    )
    if operation is None or matching is None:
        raise AIOperationRefused("insight must bind a successful observed output")
    if command.confidence is not None and not 0 <= command.confidence <= 1:
        raise AIOperationRefused("confidence must be between zero and one")
    row = AIInsight(
        tenant_id=tenant_id,
        operation_id=operation_id,
        insight_key=command.insight_key,
        insight_type=command.insight_type,
        advisory_value=command.advisory_value,
        confidence=command.confidence,
        source_output_digest=command.source_output_digest,
        status="advisory",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def acknowledge_insight(
    db: Session,
    *,
    tenant_id: UUID,
    insight_id: UUID,
    actor_ref: str,
    action_evidence: AIEvidenceBinding | None,
    actor_attribution: AIAcknowledgementAttribution | None,
    acknowledged_at: datetime,
) -> AIInsight:
    """Acknowledge an advisory insight.

    ``action_evidence``, when supplied, must be a full ``AIEvidenceBinding``
    (locator namespace, locator ref, content digest, media type) — never a
    bare locator string. ``actor_attribution``, when supplied, must
    likewise be a full ``AIAcknowledgementAttribution`` (actor namespace,
    actor type, actor ref, aware acknowledgement timestamp) — never a bare
    actor string. Both are the SAME shape of guarantee, and both are
    REFUSED rather than silently accepted as partial: THIS FUNCTION always
    writes each one's four columns together or all four ``NULL``, never
    independently, and never writes the legacy ``action_evidence_ref``
    column, nor treats ``acknowledged_by_ref``/``acknowledged_at`` as
    authoritative, from here on.

    ``acknowledged_at`` is canonicalized to UTC EXACTLY ONCE, and that
    single value — not the raw parameter, not
    ``actor_attribution.acknowledged_at`` independently — is what gets
    written to BOTH the legacy ``acknowledged_at`` column and the typed
    ``attribution_acknowledged_at`` column. If ``actor_attribution`` names
    a DIFFERENT instant than ``acknowledged_at``, this function refuses
    rather than picking one: one evaluation, one stored fact, no second
    path that can disagree — the same standard ``record_attempt``'s
    ``_canonicalize_utc`` holds for ``observed_at``.

    That is a guarantee about this one writer, not a database constraint —
    stated for both typed structs, because they hold DIFFERENT guarantees
    against the N-1 rollback window this package deliberately supports
    (round 13/14 findings, and this is stated plainly rather than left
    implied):

    - **Evidence** (``ao_0002``): the older, rolled-back application
      version can still write the legacy ``action_evidence_ref`` column
      directly, but it has NO REFERENCE to the four typed
      ``action_evidence_*`` columns — they did not exist when it was
      built. So a race can leave a row carrying both a legacy locator and
      a typed binding from different commits, but the typed binding
      itself is never overwritten by the older writer — only shadowed by
      an additionally-populated legacy locator. See
      ``authoritative_acknowledgement`` for the read-side precedence rule
      that resolves such a row.
    - **Attribution** (``ao_0003``): the older version's own
      ``acknowledge_insight`` is DIFFERENT code that predates this
      conditional UPDATE, and it mutates ``acknowledged_by_ref``/
      ``acknowledged_at``/``status`` UNCONDITIONALLY — but unlike
      evidence, those are the SAME columns both versions write; there is
      no separate legacy pair being shadowed. So the sequence nothing
      here can prevent: this version's call commits typed evidence AND
      typed attribution; the older version — still permitted to write,
      which is what expand-only rollback compatibility means — then
      flushes its own unconditional update afterward, overwriting
      ``acknowledged_by_ref``/``acknowledged_at`` with ITS actor and time,
      while never touching the four typed evidence columns OR the four
      typed attribution columns (it has no reference to either). The
      typed evidence AND the typed attribution both survive that write
      completely intact — this version's ``db.refresh(insight)`` below
      already happened before the older write lands, so it cannot even
      observe the legacy columns changing, but the columns this function
      actually treats as authoritative were never in the older writer's
      vocabulary to begin with. This is exactly why attribution needed
      its OWN new columns (Michael's ruling) rather than continuing to
      rely on the shared legacy pair: a row pairing authoritative typed
      evidence with an actor/time an N-1 writer CAN overwrite would make
      a false historical claim, and now nothing does, because nothing
      overwrites the typed attribution columns either.

    ``acknowledged_by_ref``/``acknowledged_at`` stay legacy-descriptive
    going forward, exactly like ``action_evidence_ref`` — still written
    every call, for the N-1 round-trip, but no longer read as
    authoritative by this package. See ``authoritative_acknowledgement``,
    the ONE SANCTIONED function that resolves evidence AND attribution
    together — a caller using it will not accidentally pair a fresh typed
    evidence binding with a stale or absent attribution (or vice versa),
    though reading the eight private-by-convention columns directly,
    bypassing this function, remains possible and unsupported (see
    ``AuthoritativeAcknowledgement``'s own docstring for the full honest
    statement of that limit).

    **No backfill.** A row acknowledged before ``ao_0003`` has all four
    attribution columns ``NULL`` and always will — no trustworthy typed
    actor was ever captured for it, and manufacturing one now would
    fabricate a historical claim nobody made. ``authoritative_acknowledgement``
    returns ``None`` for that row's attribution, not a synthesized one.

    The write is CONDITIONAL on the row still being ``"advisory"`` at
    UPDATE time, not merely at the SELECT above: two overlapping THIS
    FUNCTION callers can both observe ``"advisory"`` before either
    commits, and without this condition both would then unconditionally
    overwrite each other's actor, evidence and attribution, with
    whichever commits last silently winning. The conditional ``UPDATE ...
    WHERE status = 'advisory'`` lets at most one of them actually change
    the row; the other's ``rowcount`` comes back ``0`` and is reported as
    ``AIOperationRefused`` instead of silently losing its write.

    **Isolation level this failure contract assumes, stated rather than
    left implicit (round 14 finding — the same shape documented in
    ``dotmac_kernel/external_identity.py``'s "Isolation level" section):**
    the promise above — the losing caller gets ``rowcount == 0`` and
    ``AIOperationRefused`` — assumes READ COMMITTED, Postgres's default.
    Under READ COMMITTED, an ``UPDATE`` that waits on a concurrent
    writer's row lock re-reads the committed row version once the lock is
    granted, so its ``WHERE status = 'advisory'`` clause correctly
    re-evaluates against the post-commit state and returns ``rowcount ==
    0`` for the loser. Under REPEATABLE READ or SERIALIZABLE, the SAME
    statement instead raises a serialization failure — which also fails
    closed (at most one commit still holds; nothing is silently lost),
    but NOT as ``AIOperationRefused``: this function does not catch a
    serialization failure and re-raise it, so it propagates as whatever
    the database driver raises, and the caller must retry rather than
    treat every refusal here as this package's own exception type.
    """
    # `_aware` and the single `_canonicalize_utc` call below are the only
    # two operations in this function that touch caller-controlled
    # `tzinfo` behaviour (`utcoffset()`/`astimezone()`), so they are the
    # only two wrapped here — same boundary and same rationale
    # `record_attempt` already documents in full (see its docstring and
    # the comment on its own `except Exception`): a caller-supplied
    # `tzinfo` is arbitrary, untrusted code that can raise anything, not
    # only `ValueError`/`OverflowError`, and narrowing this catch has
    # already cost three rounds of a new exception type finding the gap.
    # Deliberately NOT included in this boundary: the `action_evidence`/
    # `actor_attribution` type checks below already raise
    # `AIOperationRefused` with their own specific message, and wrapping
    # them would re-wrap that message pointlessly; and no `db` call is
    # ever inside this boundary, because a broad `except Exception`
    # around database work would relabel a genuine infrastructure failure
    # as a caller-input refusal, which is a different and worse bug. The
    # later `actor_attribution.acknowledged_at != canonical_acknowledged_at`
    # comparison also does not need this boundary: by the time it runs,
    # `AIAcknowledgementAttribution.__post_init__` has already routed
    # `actor_attribution.acknowledged_at` through `_set_aware_datetime` ->
    # `_require_aware`, which reconstructs the value field by field onto
    # the plain `timezone.utc` singleton — so both operands of that
    # comparison are UTC-fixed plain datetimes and no caller `tzinfo`
    # object survives into it.
    try:
        _aware(acknowledged_at, "acknowledged_at")
        # Canonicalize EXACTLY ONCE — the same "one evaluation, one stored
        # fact" standard `record_attempt`/`_canonicalize_utc` already hold for
        # `observed_at`, now held for `acknowledged_at` too. Without this, the
        # legacy `acknowledged_at` column and the typed
        # `attribution_acknowledged_at` column would each derive from a
        # DIFFERENT value — the raw caller-supplied `acknowledged_at` for one,
        # `actor_attribution.acknowledged_at` (validated and canonicalized
        # independently, by `AIAcknowledgementAttribution.__post_init__`) for
        # the other — and nothing would stop them disagreeing on the SAME row,
        # a second path to a second truth about when the same acknowledgement
        # happened.
        canonical_acknowledged_at = _canonicalize_utc(acknowledged_at)
    except Exception as exc:  # see the comment above and `record_attempt`'s
        # docstring: `acknowledged_at` and its `tzinfo` are always
        # caller-supplied, arbitrary, untrusted input, so this catches
        # broadly rather than a named subset.
        raise AIOperationRefused(str(exc)) from exc
    if action_evidence is not None and type(action_evidence) is not AIEvidenceBinding:
        raise AIOperationRefused(
            "action_evidence must be an AIEvidenceBinding instance or None "
            "— a naked locator string is not accepted as evidence"
        )
    if (
        actor_attribution is not None
        and type(actor_attribution) is not AIAcknowledgementAttribution
    ):
        raise AIOperationRefused(
            "actor_attribution must be an AIAcknowledgementAttribution "
            "instance or None — a naked actor string is not accepted as "
            "typed attribution"
        )
    if (
        actor_attribution is not None
        and actor_attribution.acknowledged_at != canonical_acknowledged_at
    ):
        raise AIOperationRefused(
            "actor_attribution.acknowledged_at must name the same instant "
            "as acknowledged_at — a caller asserting two different times "
            "for the same acknowledgement is refused, not silently "
            "resolved in favour of either one"
        )
    insight = db.scalar(
        select(AIInsight).where(
            AIInsight.tenant_id == tenant_id,
            AIInsight.id == insight_id,
            AIInsight.status == "advisory",
        )
    )
    if insight is None or not actor_ref.strip():
        raise AIOperationRefused("advisory insight and actor are required")
    # Keys are the PYTHON ATTRIBUTE names (the private, underscore-prefixed
    # ones), not the physical column names — an ORM-enabled `update()`'s
    # `.values()` resolves string keys against the mapped class's
    # attributes, not the table's raw columns, so this must match
    # `AIInsight`'s actual attribute names for the values to land on the
    # right columns at all.
    evidence_values: dict[str, str | None]
    if action_evidence is None:
        evidence_values = {
            "_action_evidence_locator_namespace": None,
            "_action_evidence_locator_ref": None,
            "_action_evidence_content_digest": None,
            "_action_evidence_media_type": None,
        }
    else:
        evidence_values = {
            "_action_evidence_locator_namespace": action_evidence.locator_namespace,
            "_action_evidence_locator_ref": action_evidence.locator_ref,
            "_action_evidence_content_digest": action_evidence.content_digest,
            "_action_evidence_media_type": action_evidence.media_type,
        }
    attribution_values: dict[str, str | datetime | None]
    if actor_attribution is None:
        attribution_values = {
            "_attribution_actor_namespace": None,
            "_attribution_actor_type": None,
            "_attribution_actor_ref": None,
            "_attribution_acknowledged_at": None,
        }
    else:
        attribution_values = {
            "_attribution_actor_namespace": actor_attribution.actor_namespace,
            "_attribution_actor_type": actor_attribution.actor_type,
            "_attribution_actor_ref": actor_attribution.actor_ref,
            # The canonical value, not `actor_attribution.acknowledged_at`
            # — both are the SAME instant (checked above), but writing the
            # one canonicalization this function itself performed is what
            # makes that ONE evaluation the sole source for both persisted
            # timestamps, rather than two independently-canonicalized
            # values that happen to compare equal today.
            "_attribution_acknowledged_at": canonical_acknowledged_at,
        }
    outcome = db.execute(
        update(AIInsight)
        .where(
            AIInsight.tenant_id == tenant_id,
            AIInsight.id == insight_id,
            AIInsight.status == "advisory",
        )
        .values(
            status="acknowledged",
            acknowledged_by_ref=actor_ref,
            acknowledged_at=canonical_acknowledged_at,
            **evidence_values,
            **attribution_values,
        )
    )
    # `rowcount` lives on the DBAPI cursor result; the ORM's `Result` does
    # not declare it, so it is read through `cast` rather than silenced —
    # matching `dotmac_kernel/external_identity.py`'s identical idiom.
    if cast("CursorResult[Any]", outcome).rowcount != 1:
        raise AIOperationRefused(
            "insight was concurrently acknowledged by another writer"
        )
    db.refresh(insight)
    return insight


@dataclass(frozen=True, slots=True)
class AuthoritativeAcknowledgement:
    """The paired result of ``authoritative_acknowledgement``: evidence AND
    attribution resolved TOGETHER through the ONE SANCTIONED accessor, so
    a caller using it cannot accidentally combine a fresh typed evidence
    binding with a stale, absent, or mismatched attribution (or vice
    versa) — both are always returned from the SAME call against the SAME
    ``insight`` object.

    HONESTLY, stated plainly rather than left as an unqualified claim
    (this is the same shape of correction already applied to evidence
    precedence and the legacy-locator convention, and round 15 found this
    exact claim false once — the eight underlying columns were ordinary
    public attributes at the time): this is the SANCTIONED path, not an
    ENFORCED one. All eight underlying columns (``AIInsight``'s
    ``_action_evidence_*``/``_attribution_*`` attributes) are now
    PRIVATE-BY-CONVENTION, the identical treatment already given the
    legacy locator — narrowing the gap between this claim and the code,
    not only the claim itself. A caller CAN still read
    ``insight._action_evidence_locator_ref`` (or any of the other seven)
    directly, go through ``AIInsight.__table__.c[...]``, enumerate mapper
    attributes, or issue raw SQL — none of that is closed, and this
    function cannot close it. What privatizing DID close: reading
    ``insight.action_evidence_locator_ref`` (the unprefixed name) no
    longer works at all — it raises ``AttributeError`` rather than
    silently succeeding, so the ordinary, undocumented path is gone, even
    though the underscore-prefixed one remains for a caller willing to
    use it.
    """

    evidence: AIEvidenceBinding | None
    attribution: AIAcknowledgementAttribution | None


def authoritative_acknowledgement(insight: AIInsight) -> AuthoritativeAcknowledgement:
    """Resolve ``insight``'s AUTHORITATIVE action evidence AND acknowledgement
    attribution TOGETHER, applying the documented precedence rule to each.

    This is the ONE SANCTIONED accessor for both — replacing the former
    ``authoritative_action_evidence`` — precisely so a caller who uses it
    reads evidence and attribution together, not apart. Reading them
    through two separate functions would let a caller pair a fresh
    evidence resolution against a stale attribution snapshot (or the
    reverse) without any code error at all; returning them together from
    one call removes that seam FOR CALLERS OF THIS FUNCTION. It does not
    remove the seam that exists regardless: the eight underlying columns
    are private-by-convention (``_action_evidence_*``/``_attribution_*``),
    not enforced-private, so a caller reading
    ``insight._action_evidence_locator_ref`` directly (or any other one of
    the eight) still bypasses this accessor entirely — that is possible
    and unsupported, not prevented. What privatizing them DID remove: the
    ordinary, unprefixed attribute name no longer resolves at all.

    **Evidence.** The four typed ``action_evidence_*`` columns are
    authoritative whenever fully populated; the legacy
    ``action_evidence_ref`` locator is DESCRIPTIVE RESIDUE ONLY and is
    never treated as evidence — not even when it is the only thing
    populated on the row (e.g. because an old, rolled-back application
    version wrote it, or a race between an old and a new writer left both
    representations on the same row). ``evidence`` is ``None`` whenever
    the typed binding is not fully present, regardless of what the legacy
    locator holds; nothing here falls back to treating that locator as
    evidence.

    **Attribution.** The four typed ``attribution_*`` columns are
    authoritative whenever fully populated; ``acknowledged_by_ref``/
    ``acknowledged_at`` are LEGACY-DESCRIPTIVE ONLY and never treated as
    attribution — not even when they are the only thing populated on the
    row (every row acknowledged before ``ao_0003``, and every row an
    N-1 writer has touched since). ``attribution`` is ``None`` whenever
    the typed attribution is not fully present. NO BACKFILL: a
    pre-``ao_0003`` row's attribution columns are permanently ``NULL``,
    and this function returns ``None`` for it rather than manufacturing
    one from the legacy columns — no trustworthy typed actor was ever
    captured for that row.

    **DIALECT BOUNDARY, decided permanently, not a gap to fix**: this
    function REQUIRES an aware ``acknowledged_at`` and REFUSES a naive
    one (``AIAcknowledgementAttribution``'s ``_require_aware``). SQLite's
    ``DATETIME`` type discards timezone offset on every round trip, so
    calling this function against an ``AIInsight`` that went through
    ``db.refresh()`` or a fresh-session reload on SQLite RAISES
    ``ValueError`` if its typed attribution is fully populated — this is
    correct, not a defect: an acknowledgement integrity envelope binding
    actor, evidence and time must not contain a timestamp this package
    inferred rather than proved. Real PostgreSQL's ``timestamptz`` always
    returns an aware value, so the boundary holds there without any
    special handling. SQLite is a fast LOGIC harness for this package's
    test suite; it is NOT a valid PERSISTENCE round-trip for authoritative
    attribution. See ``tests/test_ai_operations_attribution_postgres.py``
    for the genuine round-trip proof.
    """
    if (
        insight._action_evidence_locator_namespace is not None
        and insight._action_evidence_locator_ref is not None
        and insight._action_evidence_content_digest is not None
        and insight._action_evidence_media_type is not None
    ):
        evidence = AIEvidenceBinding(
            locator_namespace=insight._action_evidence_locator_namespace,
            locator_ref=insight._action_evidence_locator_ref,
            content_digest=insight._action_evidence_content_digest,
            media_type=insight._action_evidence_media_type,
        )
    else:
        evidence = None

    if (
        insight._attribution_actor_namespace is not None
        and insight._attribution_actor_type is not None
        and insight._attribution_actor_ref is not None
        and insight._attribution_acknowledged_at is not None
    ):
        attribution = AIAcknowledgementAttribution(
            actor_namespace=insight._attribution_actor_namespace,
            actor_type=insight._attribution_actor_type,
            actor_ref=insight._attribution_actor_ref,
            acknowledged_at=insight._attribution_acknowledged_at,
        )
    else:
        attribution = None

    return AuthoritativeAcknowledgement(evidence=evidence, attribution=attribution)


__all__ = [
    "AIOperationRefused",
    "AuthoritativeAcknowledgement",
    "acknowledge_insight",
    "activate_policy_version",
    "authoritative_acknowledgement",
    "create_insight",
    "create_policy",
    "publish_policy_version",
    "record_attempt",
    "start_operation",
]
