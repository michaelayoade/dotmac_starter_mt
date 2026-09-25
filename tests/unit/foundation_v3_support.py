"""Synthetic trusted composition for Foundation executor behavior tests.

This is test code, not a production verifier. The synthetic attester returns a
complete V2 consumer receipt for two distinguishable materials; focused
authorization tests separately exercise pair-mismatch and provider refusal.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

from dotmac_deployment_foundation.authorization_v3 import (
    ControlConsumptionRequestV3,
    ExecutionContextV3,
    authorize_v3,
)
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_bindings import ExecutionBindings
from dotmac_deployment_foundation.execution_plan_v2 import (
    FoundationExecutionPlanV2,
    render_execution_plan_v2,
)
from dotmac_deployment_foundation.execution_plan_v3 import (
    FoundationExecutionPlanV3,
    render_execution_plan_v3,
)
from dotmac_deployment_foundation.host_source_admission import HostSourceAdmissionTrace
from dotmac_deployment_foundation.provenance import (
    AuthorizationReceiptV2,
    normalize_digest,
)

WHEEL = "sha256:" + "a" * 64
HOST_ID = "fleet-host-1"
HOST_INCARNATION = "sha256:" + "b" * 64
HOST_ENROLMENT_REF = "00000000-0000-4000-8000-000000000001"
CONTROLLER = "SHA256:test-controller"
# Control stores its plan digest as bare hex; the fixture holds that spelling
# so evidence that must persist it verbatim is actually tested.
CONTROL_PLAN = "f" * 64
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def v3_plan(base: Any, *, target_id: str = "target-1") -> FoundationExecutionPlanV3:
    """Wrap the already-rendered acts without changing their V1/V2 meaning."""
    v2 = (
        base
        if isinstance(base, FoundationExecutionPlanV2)
        else render_execution_plan_v2(base)
    )
    return render_execution_plan_v3(
        v2,
        candidate_wheel_digest=WHEEL,
        target_id=target_id,
        controller_ssh_fingerprint=CONTROLLER,
        host_id=HOST_ID,
        host_incarnation=HOST_INCARNATION,
        host_enrolment_ref=HOST_ENROLMENT_REF,
    )


@dataclasses.dataclass
class SyntheticV3Provider:
    """Test-only startup-fixed provider with independently held subject facts."""

    context: ExecutionContextV3
    receipt: AuthorizationReceiptV2
    clock: datetime = NOW
    consumed: bool = False
    approval_current: bool = True
    commit_fails: bool = False
    last_consumption_ref: str = ""
    last_trace: HostSourceAdmissionTrace | None = None
    #: Raw fields a test makes the attested Control document carry verbatim —
    #: e.g. Control's bare-hex digest spelling, which `as_document()` would
    #: otherwise normalize away before the grant ever sees it.
    attested_overrides: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def attester(self) -> SyntheticV3Provider:
        return self

    def attest_pair(
        self,
        *,
        authorization_material: dict[str, Any],
        dispatch_material: dict[str, Any],
    ) -> dict[str, Any]:
        if authorization_material != {"kind": "authorization"} or dispatch_material != {
            "kind": "dispatch"
        }:
            raise ValueError("synthetic Control pair disagrees")
        # A real Control attests the values it stores, verbatim. `as_document()`
        # normalizes every digest, so restore each digest field's raw spelling
        # (e.g. Control's bare-hex `control_plan_digest`) before any override.
        document = self.receipt.as_document()
        for key in document:
            if key.endswith("digest") and hasattr(self.receipt, key):
                document[key] = getattr(self.receipt, key)
        return {**document, **self.attested_overrides}

    def observe(self) -> ExecutionContextV3:
        return self.context

    def now(self) -> datetime:
        return self.clock

    def consume_dispatch(self, *, request: ControlConsumptionRequestV3) -> None:
        """Synthetic atomic precommit refusal, never external CP evidence."""
        if self.consumed:
            raise PreconditionFailed("Control dispatch already consumed")
        if request.authorization_material_json != b'{"kind":"authorization"}' or (
            request.dispatch_material_json != b'{"kind":"dispatch"}'
        ):
            raise PreconditionFailed("Control consumption received changed pair")
        if request.host_source_trace.opaque_finalization is None or (
            request.host_source_trace.pair_verification_result is None
        ):
            raise PreconditionFailed("Control did not receive F2 continuation")
        if normalize_digest(
            request.expected_execution_plan_digest, where="request"
        ) != normalize_digest(self.receipt.execution_plan_digest, where="receipt"):
            raise PreconditionFailed("Control plan digest changed before consumption")
        if (
            request.control_consumption_ref
            != f"control-dispatch:{self.receipt.dispatch_id}"
        ):
            raise PreconditionFailed("Control recovery coordinate changed")
        if not self.approval_current:
            raise PreconditionFailed("Control approval standing revoked")
        if self.commit_fails:
            raise PreconditionFailed("Control transaction did not commit")
        self.last_consumption_ref = request.control_consumption_ref
        self.last_trace = request.host_source_trace
        self.consumed = True


def provider_for(
    spec: Any,
    plan: FoundationExecutionPlanV3,
    *,
    now: datetime = NOW,
    receipt_overrides: dict[str, Any] | None = None,
) -> SyntheticV3Provider:
    context = ExecutionContextV3(
        product_code=spec.product,
        environment=spec.environment,
        target_id=plan.target_id,
        target_ref=plan.target,
        operation=plan.operation,
        release_ref="release-test-1",
        rollout_ref="rollout-test-1",
        plan_id="plan-test-1",
        approval_decision_ref="approval-test-1",
        control_plan_digest=CONTROL_PLAN,
        execution_sequence=int((receipt_overrides or {}).get("execution_sequence", 7)),
        attempt_no=int((receipt_overrides or {}).get("attempt_no", 1)),
        controller_ssh_fingerprint=plan.controller_ssh_fingerprint,
        host_id=plan.host_id,
        host_incarnation=plan.host_incarnation,
        host_enrolment_ref=plan.host_enrolment_ref,
    )
    fields: dict[str, Any] = {
        "authorization_envelope_digest": "sha256:" + "1" * 64,
        "dispatch_envelope_digest": "sha256:" + "2" * 64,
        "authorization_id": "authorization-test-1",
        "dispatch_id": "dispatch-test-1",
        "authorization_signer_key_id": "authorization-key",
        "authorization_signer_algorithm": "ed25519",
        "authorization_signer_public_key_fingerprint": "sha256:" + "3" * 64,
        "dispatch_signer_key_id": "dispatch-key",
        "dispatch_signer_algorithm": "ed25519",
        "dispatch_signer_public_key_fingerprint": "sha256:" + "4" * 64,
        "product_code": context.product_code,
        "environment": context.environment,
        "target_id": context.target_id,
        "target_ref": context.target_ref,
        "operation": context.operation,
        "release_ref": context.release_ref,
        "rollout_ref": context.rollout_ref,
        "plan_id": context.plan_id,
        "approval_decision_ref": context.approval_decision_ref,
        "authorization_issued_at": (NOW - timedelta(hours=1)).isoformat(),
        "authorization_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "dispatch_issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "execution_sequence": context.execution_sequence,
        "attempt_no": context.attempt_no,
        "descriptor_digest": plan.descriptor_digest,
        "execution_plan_digest": plan.digest(),
        "control_plan_digest": context.control_plan_digest,
    }
    fields.update(receipt_overrides or {})
    return SyntheticV3Provider(
        context=context, receipt=AuthorizationReceiptV2(**fields), clock=now
    )


def grant_for_plan(
    spec: Any,
    plan: FoundationExecutionPlanV3,
    *,
    now: datetime = NOW,
    receipt_overrides: dict[str, Any] | None = None,
    attested_overrides: dict[str, Any] | None = None,
):
    provider = provider_for(spec, plan, now=now, receipt_overrides=receipt_overrides)
    provider.attested_overrides = dict(attested_overrides or {})
    bindings = ExecutionBindings(
        provider="synthetic-test-host", authorization_v3_provider=provider
    )
    return authorize_v3(
        bindings=bindings,
        authorization_material={"kind": "authorization"},
        dispatch_material={"kind": "dispatch"},
        plan=plan,
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        operation=plan.operation,
        target=plan.target,
    )
