"""FakeProvisioningProvider + a reusable provider contract (Task 5).

A deterministic, in-memory `ProvisioningProvider` (implements the
`dotmac_kernel.providers.provisioning` protocol) with failure injection, call
recording, and configurable partial/resume behavior — so a consumer can test
code that DEPENDS on a provider without a real one. Plus
`check_provisioning_provider_contract`, the parametrizable suite a consumer runs
against THEIR own provider implementation to prove it honors the protocol's
idempotency / partial-resume / cancellation semantics.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from uuid import uuid4

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.providers.provisioning import (
    ApplyResult,
    CompensationDisposition,
    CompensationResult,
    ObserveResult,
    PlanResult,
    ProvisioningApplyError,
    ProvisioningPlanError,
    ProvisioningProvider,
    ProvisioningRequest,
    ProvisioningStatus,
    ProvisioningStep,
    StepStatus,
)


class FakeProvisioningProvider:
    """Deterministic in-memory provider. Config:

    - ``steps`` — the plan's step ids (all succeed unless injected otherwise).
    - ``fail_plan`` — `plan` raises `ProvisioningPlanError`.
    - ``fail_apply`` — `apply` returns a terminal FAILED result.
    - ``partial_first_apply`` — first `apply` returns PARTIAL (only the first
      step done); re-applying the same operation resumes to SUCCEEDED.

    ``calls`` records every method invocation as ``(method, key)`` tuples.
    """

    def __init__(
        self,
        *,
        steps: tuple[str, ...] = ("resource-a", "resource-b"),
        fail_plan: bool = False,
        fail_apply: bool = False,
        partial_first_apply: bool = False,
        compensation_disposition: CompensationDisposition = (
            CompensationDisposition.SUCCEEDED
        ),
    ) -> None:
        self._steps = tuple(steps)
        self._fail_plan = fail_plan
        self._fail_apply = fail_apply
        self._partial_first = partial_first_apply
        self._compensation_disposition = compensation_disposition
        self.calls: list[tuple[str, str]] = []
        self._ops: dict[str, ApplyResult] = {}

    # ── internals ───────────────────────────────────────────────────────────
    @staticmethod
    def _plan_hash(request: ProvisioningRequest) -> str:
        payload = json.dumps(
            {
                "participant": request.participant_code,
                "scope": str(request.scope),
                "intent": request.intent_id,
                "spec": dict(request.spec),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _op_id(self, request: ProvisioningRequest, plan_hash: str) -> str:
        return request.operation_id or f"{request.intent_id}:{plan_hash}"

    def _all(self, status: StepStatus) -> tuple[ProvisioningStep, ...]:
        return tuple(ProvisioningStep(step_id=s, status=status) for s in self._steps)

    # ── protocol ────────────────────────────────────────────────────────────
    def plan(self, request: ProvisioningRequest) -> PlanResult:
        self.calls.append(("plan", request.intent_id))
        if self._fail_plan:
            raise ProvisioningPlanError(f"fake plan failure for {request.intent_id!r}")
        return PlanResult(
            intent_id=request.intent_id,
            plan_hash=self._plan_hash(request),
            steps=self._all(StepStatus.PENDING),
        )

    def apply(self, request: ProvisioningRequest) -> ApplyResult:
        self.calls.append(("apply", request.intent_id))
        plan_hash = self._plan_hash(request)
        op_id = self._op_id(request, plan_hash)
        prior = self._ops.get(op_id)
        # Idempotency: re-applying a TERMINAL operation is a no-op that returns
        # the prior result unchanged.
        if prior is not None and prior.is_terminal:
            return prior

        if self._fail_apply:
            result = ApplyResult(
                intent_id=request.intent_id,
                operation_id=op_id,
                plan_hash=plan_hash,
                status=ProvisioningStatus.FAILED,
                steps=self._all(StepStatus.FAILED),
            )
        elif self._partial_first and prior is None:
            # First apply: partial — first step done, the rest outstanding.
            steps = (
                ProvisioningStep(self._steps[0], StepStatus.SUCCEEDED),
                *(ProvisioningStep(s, StepStatus.PENDING) for s in self._steps[1:]),
            )
            result = ApplyResult(
                intent_id=request.intent_id,
                operation_id=op_id,
                plan_hash=plan_hash,
                status=ProvisioningStatus.PARTIAL,
                steps=steps,
            )
        else:
            # Fresh full apply OR resume of a partial → everything converges.
            result = ApplyResult(
                intent_id=request.intent_id,
                operation_id=op_id,
                plan_hash=plan_hash,
                status=ProvisioningStatus.SUCCEEDED,
                steps=self._all(StepStatus.SUCCEEDED),
            )
        self._ops[op_id] = result
        return result

    def observe(self, operation_id: str) -> ObserveResult:
        self.calls.append(("observe", operation_id))
        prior = self._ops.get(operation_id)
        if prior is None:
            return ObserveResult(
                intent_id="",
                operation_id=operation_id,
                status=ProvisioningStatus.PENDING,
            )
        return ObserveResult(
            intent_id=prior.intent_id,
            operation_id=operation_id,
            status=prior.status,
            steps=prior.steps,
            plan_hash=prior.plan_hash,
        )

    def cancel(self, operation_id: str) -> ObserveResult:
        self.calls.append(("cancel", operation_id))
        prior = self._ops.get(operation_id)
        steps = tuple(
            ProvisioningStep(
                s.step_id,
                s.status if s.is_settled else StepStatus.CANCELLED,
            )
            for s in (prior.steps if prior else ())
        )
        if prior is not None:
            # Record a terminal CANCELLED result so a later apply is idempotent.
            self._ops[operation_id] = ApplyResult(
                intent_id=prior.intent_id,
                operation_id=operation_id,
                plan_hash=prior.plan_hash,
                status=ProvisioningStatus.CANCELLED,
                steps=steps,
            )
        return ObserveResult(
            intent_id=prior.intent_id if prior else "",
            operation_id=operation_id,
            status=ProvisioningStatus.CANCELLED,
            steps=steps,
            plan_hash=prior.plan_hash if prior else None,
        )

    def compensate(self, operation_id: str, reason: str) -> CompensationResult:
        self.calls.append(("compensate", operation_id))
        if not reason.strip():
            raise ValueError("compensation reason must not be blank")
        snapshot = self.observe(operation_id)
        return CompensationResult(
            operation_id=operation_id,
            disposition=self._compensation_disposition,
            snapshot=snapshot,
            reason_code=(
                None
                if self._compensation_disposition is CompensationDisposition.SUCCEEDED
                else self._compensation_disposition.value
            ),
        )


def check_provisioning_provider_contract(
    make_provider: Callable[..., ProvisioningProvider],
) -> None:
    """Assert a provider factory yields implementations that honor the protocol.

    `make_provider(**kwargs)` returns a fresh provider; recognized kwargs are the
    `FakeProvisioningProvider` config (`fail_apply`, `partial_first_apply`) — a
    real provider's factory should accept and honor the same behavioral knobs,
    or wrap itself so the contract can drive them. Run this from a consumer's
    test suite: `check_provisioning_provider_contract(MyProvider.for_tests)`."""
    req = ProvisioningRequest(
        participant_code="fake.provisioning",
        scope=TenantScope(uuid4()),
        intent_id="i-1",
        spec={"size": 1},
    )

    # Structural conformance.
    provider = make_provider()
    assert isinstance(provider, ProvisioningProvider)

    # plan is deterministic in plan_hash for the same request.
    assert provider.plan(req).plan_hash == make_provider().plan(req).plan_hash

    # A clean apply reaches a terminal SUCCEEDED and is idempotent on re-apply.
    applied = provider.apply(req)
    assert applied.status is ProvisioningStatus.SUCCEEDED
    assert applied.is_terminal
    again = provider.apply(req)
    assert again.operation_id == applied.operation_id
    assert again.status is applied.status

    # observe reflects the recorded operation.
    observed = provider.observe(applied.operation_id)
    assert observed.status is ProvisioningStatus.SUCCEEDED

    # Partial apply is RESUMABLE: re-applying the same operation converges.
    partial_provider = make_provider(partial_first_apply=True)
    first = partial_provider.apply(req)
    assert first.is_partial
    assert first.outstanding_steps  # something remains
    resumed = partial_provider.apply(
        ProvisioningRequest(
            participant_code=req.participant_code,
            scope=req.scope,
            intent_id="i-1",
            spec={"size": 1},
            operation_id=first.operation_id,
        )
    )
    assert resumed.status is ProvisioningStatus.SUCCEEDED
    assert not resumed.outstanding_steps

    # Failure injection → terminal FAILED, not a partial.
    failing = make_provider(fail_apply=True)
    failed = failing.apply(req)
    assert failed.status is ProvisioningStatus.FAILED
    assert failed.is_terminal

    # plan failure raises the stable terminal error.
    plan_failing = make_provider(fail_plan=True)
    try:
        plan_failing.plan(req)
    except ProvisioningPlanError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ProvisioningPlanError")

    # cancel settles to a terminal CANCELLED snapshot.
    cancel_provider = make_provider()
    op = cancel_provider.apply(req).operation_id
    snapshot = cancel_provider.cancel(op)
    assert snapshot.status is ProvisioningStatus.CANCELLED

    # Compensation is a distinct, explicit decision after settlement.
    compensation_provider = make_provider()
    compensated_op = compensation_provider.apply(req).operation_id
    compensation = compensation_provider.compensate(
        compensated_op, "conformance reversal"
    )
    assert compensation.operation_id == compensated_op
    assert compensation.disposition is CompensationDisposition.SUCCEEDED

    # The apply/plan error classification is stable.
    assert ProvisioningApplyError("x").retryable is False


__all__ = ["FakeProvisioningProvider", "check_provisioning_provider_contract"]
