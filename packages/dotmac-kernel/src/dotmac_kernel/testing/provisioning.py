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

from dotmac_kernel.providers.provisioning import (
    ApplyResult,
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
    ) -> None:
        self._steps = tuple(steps)
        self._fail_plan = fail_plan
        self._fail_apply = fail_apply
        self._partial_first = partial_first_apply
        self.calls: list[tuple[str, str]] = []
        self._ops: dict[str, ApplyResult] = {}

    # ── internals ───────────────────────────────────────────────────────────
    @staticmethod
    def _plan_hash(request: ProvisioningRequest) -> str:
        payload = json.dumps(
            {"intent": request.intent_id, "spec": dict(request.spec)}, sort_keys=True
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


def _require(condition: object, requirement: str) -> None:
    """Raise `AssertionError` unless `condition` holds.

    Deliberately NOT `assert`. This function is the body of a PUBLIC conformance
    suite that consumers run against their own providers, and `python -O` strips
    every `assert` statement — which would turn the whole contract into a
    no-op that returns cleanly. A conformance run that passes because it checked
    nothing is worse than no conformance run at all: it produces a green signal
    for a provider nobody verified.

    `AssertionError` rather than a bespoke type so that pytest, unittest and a
    bare `python -c` all report it the way a consumer expects.
    """
    if not condition:
        raise AssertionError(f"provisioning provider contract violated — {requirement}")


def check_provisioning_provider_contract(
    make_provider: Callable[..., ProvisioningProvider],
) -> None:
    """Assert a provider factory yields implementations that honor the protocol.

    `make_provider(**kwargs)` returns a fresh provider; recognized kwargs are the
    `FakeProvisioningProvider` config (`fail_apply`, `partial_first_apply`) — a
    real provider's factory should accept and honor the same behavioral knobs,
    or wrap itself so the contract can drive them. Run this from a consumer's
    test suite: `check_provisioning_provider_contract(MyProvider.for_tests)`.

    Raises `AssertionError` naming the violated requirement. Every check is an
    explicit raise (see `_require`), so the suite keeps working under
    `python -O`.
    """
    req = ProvisioningRequest(intent_id="i-1", spec={"size": 1})

    # Structural conformance.
    provider = make_provider()
    _require(
        isinstance(provider, ProvisioningProvider),
        "the factory must return an object satisfying the ProvisioningProvider "
        "protocol (plan/apply/observe/cancel)",
    )

    # plan is deterministic in plan_hash for the same request.
    _require(
        provider.plan(req).plan_hash == make_provider().plan(req).plan_hash,
        "plan() must be deterministic: the same request must yield the same "
        "plan_hash across two fresh providers, since apply() keys idempotency "
        "off it",
    )

    # A clean apply reaches a terminal SUCCEEDED and is idempotent on re-apply.
    applied = provider.apply(req)
    _require(
        applied.status is ProvisioningStatus.SUCCEEDED,
        f"a clean apply() must reach SUCCEEDED, got {applied.status}",
    )
    _require(applied.is_terminal, "a SUCCEEDED apply() result must be terminal")
    again = provider.apply(req)
    _require(
        again.operation_id == applied.operation_id,
        "re-applying a terminal operation must be a no-op returning the SAME "
        "operation_id, not a new operation",
    )
    _require(
        again.status is applied.status,
        "re-applying a terminal operation must return the prior status " "unchanged",
    )

    # observe reflects the recorded operation.
    observed = provider.observe(applied.operation_id)
    _require(
        observed.status is ProvisioningStatus.SUCCEEDED,
        "observe() must report the recorded status of a settled operation",
    )

    # Partial apply is RESUMABLE: re-applying the same operation converges.
    partial_provider = make_provider(partial_first_apply=True)
    first = partial_provider.apply(req)
    _require(
        first.is_partial,
        "a provider configured for a partial first apply must return PARTIAL, "
        "which is a first-class result and not an error",
    )
    _require(
        first.outstanding_steps,
        "a PARTIAL result must name what remains in outstanding_steps — that "
        "is what a resume reconciles",
    )
    resumed = partial_provider.apply(
        ProvisioningRequest(
            intent_id="i-1", spec={"size": 1}, operation_id=first.operation_id
        )
    )
    _require(
        resumed.status is ProvisioningStatus.SUCCEEDED,
        "re-applying a PARTIAL operation_id must RESUME it to SUCCEEDED, not "
        "start a new operation",
    )
    _require(
        not resumed.outstanding_steps,
        "a resumed operation that SUCCEEDED must leave no outstanding steps",
    )

    # Failure injection → terminal FAILED, not a partial.
    failing = make_provider(fail_apply=True)
    failed = failing.apply(req)
    _require(
        failed.status is ProvisioningStatus.FAILED,
        f"an injected apply failure must settle to FAILED, got {failed.status}",
    )
    _require(failed.is_terminal, "a FAILED apply() result must be terminal")

    # plan failure raises the stable terminal error.
    plan_failing = make_provider(fail_plan=True)
    try:
        plan_failing.plan(req)
    except ProvisioningPlanError:
        pass
    else:
        raise AssertionError(
            "provisioning provider contract violated — an invalid spec must "
            "raise ProvisioningPlanError (terminal), not return a result"
        )

    # cancel settles to a terminal CANCELLED snapshot.
    cancel_provider = make_provider()
    op = cancel_provider.apply(req).operation_id
    snapshot = cancel_provider.cancel(op)
    _require(
        snapshot.status is ProvisioningStatus.CANCELLED,
        "cancel() must return a snapshot settling to CANCELLED — cancellation "
        "is cooperative, but the outcome is still terminal",
    )

    # The apply/plan error classification is stable.
    _require(
        ProvisioningApplyError("x").retryable is False,
        "ProvisioningApplyError must classify as non-retryable; a caller "
        "branches on `retryable` to decide whether to retry the operation",
    )


__all__ = ["FakeProvisioningProvider", "check_provisioning_provider_contract"]
