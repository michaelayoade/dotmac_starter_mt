"""The sole V3 execution-grant issuer for a trusted Control V2 pair.

The provider is installed as part of the assembly's fixed execution bindings.
Request material may supply the signed pair, never its attester, clock, or
host/context observer.  The observer must obtain fresh local/Control facts;
none of its fields may be copied from the receipt being checked.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from .authorization import _ISSUED, OPERATIONS, ExecutionGrant
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .execution_plan_v3 import (
    FoundationExecutionPlanV3,
    require_execution_plan_v3_digest,
)
from .host_source import read_installed_artifact
from .host_source_admission import HostSourceAdmissionTrace
from .provenance import (
    AuthorizationReceiptV2,
    AuthorizationReceiptV2Attester,
    attest_authorization_receipt_v2,
    normalize_digest,
)

if TYPE_CHECKING:
    from .execution_bindings import ExecutionBindings

__all__ = [
    "ExecutionContextV3",
    "ControlConsumptionRequestV3",
    "ExecutionAuthorityV3Provider",
    "authorize_v3",
]


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionContextV3:
    """Independent, fresh execution facts from the composed CP/host adapter."""

    product_code: str
    environment: str
    target_id: str
    target_ref: str
    operation: str
    release_ref: str
    rollout_ref: str
    plan_id: str
    approval_decision_ref: str
    control_plan_digest: str
    execution_sequence: int
    attempt_no: int
    controller_ssh_fingerprint: str
    host_id: str
    host_incarnation: str
    host_enrolment_ref: str

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if field.name in {"execution_sequence", "attempt_no"}:
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise SpecError(f"ExecutionContextV3.{field.name} must be positive")
            elif not isinstance(value, str) or not value.strip():
                raise SpecError(f"ExecutionContextV3.{field.name} is empty")
        normalize_digest(self.control_plan_digest, where="control_plan_digest")
        try:
            canonical_enrolment = str(UUID(self.host_enrolment_ref))
        except ValueError as exc:
            raise SpecError(
                "ExecutionContextV3.host_enrolment_ref must be a UUID"
            ) from exc
        if canonical_enrolment != self.host_enrolment_ref:
            raise SpecError("ExecutionContextV3.host_enrolment_ref must be canonical")


@dataclasses.dataclass(frozen=True, slots=True)
class ControlConsumptionRequestV3:
    """Exact pair and coordinates, with a deterministic Control ledger key."""

    authorization_material_json: bytes
    dispatch_material_json: bytes
    expected_receipt: AuthorizationReceiptV2
    expected_context: ExecutionContextV3
    expected_execution_plan_digest: str
    control_consumption_ref: str


@runtime_checkable
class ExecutionAuthorityV3Provider(Protocol):
    """Startup-installed trust and observation; never selected by a request."""

    @property
    def attester(self) -> AuthorizationReceiptV2Attester: ...

    def observe(self) -> ExecutionContextV3: ...

    def now(self) -> datetime: ...

    def consume_dispatch(self, *, request: ControlConsumptionRequestV3) -> None:
        """Validate exact request/current standing and consume atomically.

        Refuse before commit or return only after committing the exact dispatch
        and deterministic ledger coordinate. The caller checks no result.
        """
        ...


def _canonical_material(material: Mapping[str, Any], *, name: str) -> bytes:
    """Immutable full-value snapshot; no later request mutation can swap it."""
    try:
        return json.dumps(
            dict(material), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{name} must be a JSON-compatible Control document") from exc


def authorize_v3(
    *,
    bindings: ExecutionBindings | None,
    authorization_material: Mapping[str, Any],
    dispatch_material: Mapping[str, Any],
    plan: FoundationExecutionPlanV3,
    descriptor_digest: str,
    operation: str,
    target: str,
) -> ExecutionGrant:
    """Issue one grant only after trusted pair, fresh facts and V3 digest agree.

    ``bindings`` is trusted in-process assembly composition, not proof of
    provenance by its Python type. The installed CLI discovers exactly one
    bindings distribution and exposes no request field for replacing it.
    """
    if bindings is None:
        raise PreconditionFailed(
            "V3 execution authority requires startup-fixed assembly bindings"
        )
    provider = bindings.authorization_v3_provider
    if provider is None:
        raise PreconditionFailed("no trusted V2 authorization provider is installed")
    if not isinstance(plan, FoundationExecutionPlanV3):
        raise PreconditionFailed("V1/V2 execution plans cannot issue a grant")
    if operation not in OPERATIONS or plan.operation != operation:
        raise PreconditionFailed("execution operation disagrees with the V3 plan")
    wanted_digest = normalize_digest(descriptor_digest, where="descriptor_digest")
    if (
        normalize_digest(plan.descriptor_digest, where="plan descriptor")
        != wanted_digest
    ):
        raise PreconditionFailed("descriptor in hand disagrees with the V3 plan")
    if plan.target != target:
        raise PreconditionFailed("target in hand disagrees with the V3 plan")

    facts = require_current_subject_v3(plan=plan, provider=provider)
    if facts.target_ref != target:
        raise PreconditionFailed("observed target differs from execution target")
    if facts.operation != operation:
        raise PreconditionFailed("observed operation disagrees with execution request")

    authorization_snapshot = _canonical_material(
        authorization_material, name="authorization material"
    )
    dispatch_snapshot = _canonical_material(dispatch_material, name="dispatch material")
    attested = attest_authorization_receipt_v2(
        json.loads(authorization_snapshot),
        json.loads(dispatch_snapshot),
        attester=provider.attester,
    )
    now = provider.now()
    attested.require_execution_inputs(
        now=now,
        product_code=facts.product_code,
        environment=facts.environment,
        target_id=facts.target_id,
        target_ref=facts.target_ref,
        operation=operation,
        release_ref=facts.release_ref,
        rollout_ref=facts.rollout_ref,
        plan_id=facts.plan_id,
        approval_decision_ref=facts.approval_decision_ref,
        control_plan_digest=facts.control_plan_digest,
        descriptor_digest=wanted_digest,
        execution_plan_digest=plan.digest(),
        execution_sequence=facts.execution_sequence,
        attempt_no=facts.attempt_no,
    )
    receipt = attested._receipt_for_foundation_grant()
    require_execution_plan_v3_digest(plan, authorized=receipt.execution_plan_digest)
    return ExecutionGrant(
        _ISSUED,
        operation=operation,
        descriptor_digest=wanted_digest,
        target=target,
        execution_plan_digest=plan.digest(),
        execution_sequence=facts.execution_sequence,
        attempt_no=facts.attempt_no,
        receipt=receipt,
        v3_provider=provider,
        authorization_material_json=authorization_snapshot,
        dispatch_material_json=dispatch_snapshot,
    )


def require_committed_consumption_v3(
    *,
    grant: ExecutionGrant,
    plan: FoundationExecutionPlanV3,
    facts: ExecutionContextV3,
    trace: HostSourceAdmissionTrace,
) -> str:
    """Check every Foundation fact, then let CP atomically consume and commit.

    The fixed provider either raises before commit or returns normally after
    commit. Foundation performs no fallible work after that call. Control's
    committed ledger, keyed by the verified dispatch ID, is the recovery
    source if the process crashes before local outcome evidence is written.
    """
    fresh = require_current_subject_v3(plan=plan, provider=grant.v3_provider)
    if fresh != facts:
        raise PreconditionFailed("execution subject moved before Control consumption")
    if (
        trace.host_identity != facts.host_id
        or trace.installed_signer_fingerprint != facts.host_incarnation
        or trace.installed_trust_root_version != facts.host_enrolment_ref
        or trace.host_observation_id != grant.receipt.dispatch_id
    ):
        raise PreconditionFailed("F2 admitted host disagrees with Control dispatch")
    digest = plan.digest()
    if (
        grant.receipt.execution_plan_digest != digest
        or grant.receipt.execution_sequence != facts.execution_sequence
        or grant.receipt.attempt_no != facts.attempt_no
        or grant.execution_sequence != facts.execution_sequence
        or grant.attempt_no != facts.attempt_no
    ):
        raise PreconditionFailed("Control replay or V3 plan coordinate changed")
    grant.receipt._require_live(now=grant.v3_provider.now())
    grant.receipt._require_execution_inputs(
        product_code=fresh.product_code,
        environment=fresh.environment,
        target_id=fresh.target_id,
        target_ref=fresh.target_ref,
        operation=fresh.operation,
        release_ref=fresh.release_ref,
        rollout_ref=fresh.rollout_ref,
        plan_id=fresh.plan_id,
        approval_decision_ref=fresh.approval_decision_ref,
        control_plan_digest=fresh.control_plan_digest,
        descriptor_digest=plan.descriptor_digest,
        execution_plan_digest=digest,
        execution_sequence=fresh.execution_sequence,
        attempt_no=fresh.attempt_no,
    )
    recovery_ref = f"control-dispatch:{grant.receipt.dispatch_id}"
    request = ControlConsumptionRequestV3(
        authorization_material_json=grant.authorization_material_json,
        dispatch_material_json=grant.dispatch_material_json,
        expected_receipt=grant.receipt,
        expected_context=fresh,
        expected_execution_plan_digest=digest,
        control_consumption_ref=recovery_ref,
    )
    grant.v3_provider.consume_dispatch(request=request)
    return recovery_ref


def require_current_subject_v3(
    *, plan: FoundationExecutionPlanV3, provider: ExecutionAuthorityV3Provider
) -> ExecutionContextV3:
    """Reobserve independent facts at issuance and again before effects."""
    installed = read_installed_artifact()
    if installed.artifact_digest != Digest.parse(plan.candidate_wheel_digest):
        raise PreconditionFailed("installed Foundation wheel is not the V3 candidate")
    facts = provider.observe()
    if not isinstance(facts, ExecutionContextV3):
        raise PreconditionFailed("trusted provider returned no typed execution facts")
    for name, from_plan, observed in (
        ("target_id", plan.target_id, facts.target_id),
        ("target_ref", plan.target, facts.target_ref),
        (
            "controller_ssh_fingerprint",
            plan.controller_ssh_fingerprint,
            facts.controller_ssh_fingerprint,
        ),
        ("host_id", plan.host_id, facts.host_id),
        ("host_incarnation", plan.host_incarnation, facts.host_incarnation),
        ("host_enrolment_ref", plan.host_enrolment_ref, facts.host_enrolment_ref),
    ):
        if from_plan != observed:
            raise PreconditionFailed(
                f"V3 plan {name} disagrees with observed host facts"
            )
    return facts
